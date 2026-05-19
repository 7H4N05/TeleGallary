"""
Health Monitor — system-level health metrics collection.

Continuously samples:
- CPU usage
- RAM usage (absolute + trend)
- Event loop lag (how delayed asyncio tasks are)
- SQLite query latency
- Worker queue depth
- Pyrogram client connection state

These metrics feed the Failure Detector and Adaptive Controller.
"""

from __future__ import annotations

import asyncio
import gc
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# psutil is optional but highly recommended
try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    logger.warning("psutil not installed — system health monitoring limited")


@dataclass
class HealthSnapshot:
    """Point-in-time health reading."""
    timestamp: float = 0.0
    cpu_percent: float = 0.0
    memory_rss_mb: float = 0.0
    memory_percent: float = 0.0
    memory_trend: str = "stable"       # stable, growing, critical
    event_loop_lag_ms: float = 0.0
    sqlite_latency_ms: float = 0.0
    gc_gen0_collections: int = 0
    gc_gen1_collections: int = 0
    gc_gen2_collections: int = 0
    worker_queue_depth: int = 0
    telegram_clients_connected: int = 0
    telegram_clients_total: int = 0
    is_healthy: bool = True
    issues: list = field(default_factory=list)


class HealthMonitor:
    """Samples system health metrics on a periodic schedule.

    Usage:
        monitor = HealthMonitor()
        await monitor.start(interval=10)  # sample every 10s
        ...
        snap = monitor.latest
    """

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._latest: HealthSnapshot = HealthSnapshot()
        self._memory_history: list[float] = []  # last N RSS readings
        self._max_memory_history = 60           # ~10 min at 10s interval
        self._process: Optional[object] = None

        if _HAS_PSUTIL:
            self._process = psutil.Process(os.getpid())

    @property
    def latest(self) -> HealthSnapshot:
        return self._latest

    async def start(self, interval: float = 10.0):
        """Start the background health-sampling loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(interval))
        logger.info("Health monitor started", interval_sec=interval)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, interval: float):
        while True:
            try:
                snap = await self._sample()
                self._latest = snap
            except Exception as e:
                logger.warning("Health sample failed", error=str(e))
            await asyncio.sleep(interval)

    async def _sample(self) -> HealthSnapshot:
        snap = HealthSnapshot(timestamp=time.time())

        # ── CPU & Memory via psutil ──────────────────────────────────
        if _HAS_PSUTIL and self._process:
            try:
                snap.cpu_percent = self._process.cpu_percent(interval=0)
                mem = self._process.memory_info()
                snap.memory_rss_mb = mem.rss / (1024 * 1024)
                snap.memory_percent = self._process.memory_percent()
            except Exception:
                pass

        # Track memory trend
        self._memory_history.append(snap.memory_rss_mb)
        if len(self._memory_history) > self._max_memory_history:
            self._memory_history = self._memory_history[-self._max_memory_history:]

        snap.memory_trend = self._compute_memory_trend()

        # ── Event loop lag ───────────────────────────────────────────
        snap.event_loop_lag_ms = await self._measure_event_loop_lag()

        # ── GC stats ─────────────────────────────────────────────────
        gc_stats = gc.get_stats()
        if len(gc_stats) >= 3:
            snap.gc_gen0_collections = gc_stats[0].get("collections", 0)
            snap.gc_gen1_collections = gc_stats[1].get("collections", 0)
            snap.gc_gen2_collections = gc_stats[2].get("collections", 0)

        # ── SQLite latency ───────────────────────────────────────────
        snap.sqlite_latency_ms = await self._measure_sqlite_latency()

        # ── Telegram client state ────────────────────────────────────
        try:
            from telegram.client_manager import get_client_manager
            mgr = get_client_manager()
            total = len(mgr._clients)
            connected = sum(
                1 for m in mgr._clients.values() if m.client.is_connected
            )
            snap.telegram_clients_total = total
            snap.telegram_clients_connected = connected
        except Exception:
            pass

        # ── Assess overall health ────────────────────────────────────
        issues = []
        if snap.cpu_percent > 90:
            issues.append("CPU usage critical (>90%)")
        if snap.memory_rss_mb > 2048:
            issues.append(f"High memory usage ({snap.memory_rss_mb:.0f} MB)")
        if snap.memory_trend == "critical":
            issues.append("Continuous memory growth detected (possible leak)")
        if snap.event_loop_lag_ms > 500:
            issues.append(f"Event loop lag {snap.event_loop_lag_ms:.0f}ms")
        if snap.sqlite_latency_ms > 200:
            issues.append(f"SQLite slow ({snap.sqlite_latency_ms:.0f}ms)")
        if snap.telegram_clients_total > 0 and snap.telegram_clients_connected == 0:
            issues.append("All Telegram clients disconnected")

        snap.issues = issues
        snap.is_healthy = len(issues) == 0
        return snap

    def _compute_memory_trend(self) -> str:
        """Detect continuous memory growth over the sampling window."""
        history = self._memory_history
        if len(history) < 10:
            return "stable"

        # Check if memory has grown >20% over the window
        oldest = history[0]
        newest = history[-1]
        if oldest <= 0:
            return "stable"

        growth = (newest - oldest) / oldest
        if growth > 0.5:
            return "critical"
        if growth > 0.2:
            return "growing"
        return "stable"

    async def _measure_event_loop_lag(self) -> float:
        """Measure how long it takes for a trivial coroutine to run."""
        start = time.monotonic()
        await asyncio.sleep(0)  # yield to event loop
        return (time.monotonic() - start) * 1000  # ms

    async def _measure_sqlite_latency(self) -> float:
        """Run a trivial query and measure round-trip time."""
        try:
            from db.database import AsyncSessionLocal
            start = time.monotonic()
            async with AsyncSessionLocal() as db:
                await db.execute("SELECT 1")
            return (time.monotonic() - start) * 1000  # ms
        except Exception:
            return -1.0

    def snapshot_dict(self) -> Dict:
        """Return a JSON-serializable dict of latest health."""
        s = self._latest
        return {
            "timestamp": s.timestamp,
            "cpu_percent": round(s.cpu_percent, 1),
            "memory_rss_mb": round(s.memory_rss_mb, 1),
            "memory_percent": round(s.memory_percent, 1),
            "memory_trend": s.memory_trend,
            "event_loop_lag_ms": round(s.event_loop_lag_ms, 2),
            "sqlite_latency_ms": round(s.sqlite_latency_ms, 2),
            "gc_gen0": s.gc_gen0_collections,
            "gc_gen1": s.gc_gen1_collections,
            "gc_gen2": s.gc_gen2_collections,
            "telegram_clients_connected": s.telegram_clients_connected,
            "telegram_clients_total": s.telegram_clients_total,
            "is_healthy": s.is_healthy,
            "issues": s.issues,
        }


# ── Global singleton ────────────────────────────────────────────────────
_monitor: Optional[HealthMonitor] = None


def get_health_monitor() -> HealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor
