"""
Failure Detector — identifies upload stalls, worker hangs, and anomalous
error patterns before they cascade into full session failure.

Detection capabilities
======================
1. **Upload stall**: No progress for configurable threshold (default 5 min)
2. **Retry storm**: Retry rate exceeds threshold → likely systemic issue
3. **FloodWait escalation**: Increasing FloodWait durations → backoff needed
4. **Client disconnect cluster**: Multiple reconnects in short window
5. **Memory pressure**: Continuous growth detected by HealthMonitor
6. **Event loop saturation**: Lag above threshold → CPU-bound work blocking
7. **Queue deadlock**: Queue depth growing but no work completing

Each detection emits a `FailureEvent` consumed by the Recovery Engine.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from monitoring.metrics import UploadMetrics
from monitoring.health_monitor import HealthMonitor
from utils.logger import get_logger

logger = get_logger(__name__)


class FailureSeverity(str, Enum):
    WARNING = "warning"       # advisory, may self-resolve
    DEGRADED = "degraded"     # performance impact, action recommended
    CRITICAL = "critical"     # upload at risk, immediate action needed


class FailureType(str, Enum):
    UPLOAD_STALL = "upload_stall"
    RETRY_STORM = "retry_storm"
    FLOODWAIT_ESCALATION = "floodwait_escalation"
    CLIENT_DISCONNECT = "client_disconnect"
    MEMORY_PRESSURE = "memory_pressure"
    EVENT_LOOP_SATURATION = "event_loop_saturation"
    QUEUE_DEADLOCK = "queue_deadlock"
    THROUGHPUT_COLLAPSE = "throughput_collapse"


@dataclass
class FailureEvent:
    failure_type: FailureType
    severity: FailureSeverity
    message: str
    timestamp: float = field(default_factory=time.time)
    context: Dict = field(default_factory=dict)


# ── Thresholds (can be tuned via settings) ──────────────────────────────
@dataclass
class DetectorThresholds:
    stall_seconds: float = 300.0             # 5 minutes no progress
    retry_rate_per_min: float = 10.0         # retries/min to trigger alarm
    floodwait_escalation_count: int = 3      # N floodwaits in window → alarm
    floodwait_window_seconds: float = 600.0  # window for escalation check
    reconnect_cluster_count: int = 3         # N reconnects in window → alarm
    reconnect_window_seconds: float = 300.0
    event_loop_lag_warning_ms: float = 200.0
    event_loop_lag_critical_ms: float = 1000.0
    throughput_collapse_ratio: float = 0.2   # <20% of rolling mean → collapse
    check_interval_seconds: float = 15.0


class FailureDetector:
    """Runs as a background task, periodically checking for failure conditions.

    On detection, calls the registered `on_failure` callback (typically wired
    to the Recovery Engine).
    """

    def __init__(
        self,
        metrics: UploadMetrics,
        health: HealthMonitor,
        thresholds: Optional[DetectorThresholds] = None,
    ):
        self._metrics = metrics
        self._health = health
        self._thresholds = thresholds or DetectorThresholds()
        self._on_failure: Optional[Callable[[FailureEvent], asyncio.Future]] = None
        self._task: Optional[asyncio.Task] = None
        self._events: List[FailureEvent] = []
        self._last_retry_count = 0
        self._last_check_time = time.monotonic()
        self._floodwait_timestamps: List[float] = []
        self._reconnect_timestamps: List[float] = []
        self._running = False

    def set_failure_handler(self, handler: Callable):
        """Register the callback (async) invoked on failure detection."""
        self._on_failure = handler

    @property
    def recent_events(self) -> List[FailureEvent]:
        return list(self._events[-50:])  # last 50

    def record_floodwait(self):
        self._floodwait_timestamps.append(time.time())

    def record_reconnect(self):
        self._reconnect_timestamps.append(time.time())

    async def start(self):
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Failure detector started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self):
        while self._running:
            try:
                await self._check_all()
            except Exception as e:
                logger.warning("Failure detector check error", error=str(e))
            await asyncio.sleep(self._thresholds.check_interval_seconds)

    async def _check_all(self):
        t = self._thresholds
        now = time.time()
        m = self._metrics
        h = self._health.latest

        # ── 1. Upload stall ──────────────────────────────────────────
        if m.check_stall(t.stall_seconds):
            await self._emit(FailureEvent(
                failure_type=FailureType.UPLOAD_STALL,
                severity=FailureSeverity.CRITICAL,
                message=f"No upload progress for {m.stall.stall_duration:.0f}s",
                context={
                    "stall_duration": m.stall.stall_duration,
                    "consecutive": m.stall.consecutive_stalls,
                    "last_uploaded": m.stall.last_uploaded_count,
                },
            ))

        # ── 2. Retry storm ───────────────────────────────────────────
        elapsed_since_last = time.monotonic() - self._last_check_time
        if elapsed_since_last > 0:
            retry_delta = m.total_retries - self._last_retry_count
            retry_rate = retry_delta / (elapsed_since_last / 60)
            if retry_rate > t.retry_rate_per_min:
                await self._emit(FailureEvent(
                    failure_type=FailureType.RETRY_STORM,
                    severity=FailureSeverity.DEGRADED,
                    message=f"Retry rate {retry_rate:.1f}/min exceeds threshold",
                    context={"retry_rate": retry_rate, "threshold": t.retry_rate_per_min},
                ))
        self._last_retry_count = m.total_retries
        self._last_check_time = time.monotonic()

        # ── 3. FloodWait escalation ──────────────────────────────────
        cutoff = now - t.floodwait_window_seconds
        self._floodwait_timestamps = [
            ts for ts in self._floodwait_timestamps if ts >= cutoff
        ]
        if len(self._floodwait_timestamps) >= t.floodwait_escalation_count:
            await self._emit(FailureEvent(
                failure_type=FailureType.FLOODWAIT_ESCALATION,
                severity=FailureSeverity.DEGRADED,
                message=f"{len(self._floodwait_timestamps)} FloodWaits in {t.floodwait_window_seconds}s",
                context={"count": len(self._floodwait_timestamps)},
            ))

        # ── 4. Client disconnect cluster ─────────────────────────────
        cutoff = now - t.reconnect_window_seconds
        self._reconnect_timestamps = [
            ts for ts in self._reconnect_timestamps if ts >= cutoff
        ]
        if len(self._reconnect_timestamps) >= t.reconnect_cluster_count:
            await self._emit(FailureEvent(
                failure_type=FailureType.CLIENT_DISCONNECT,
                severity=FailureSeverity.CRITICAL,
                message=f"{len(self._reconnect_timestamps)} reconnects in {t.reconnect_window_seconds}s",
                context={"count": len(self._reconnect_timestamps)},
            ))

        # ── 5. Memory pressure ───────────────────────────────────────
        if h.memory_trend == "critical":
            await self._emit(FailureEvent(
                failure_type=FailureType.MEMORY_PRESSURE,
                severity=FailureSeverity.DEGRADED,
                message=f"Continuous memory growth: {h.memory_rss_mb:.0f} MB",
                context={"rss_mb": h.memory_rss_mb, "trend": h.memory_trend},
            ))

        # ── 6. Event loop saturation ─────────────────────────────────
        if h.event_loop_lag_ms > t.event_loop_lag_critical_ms:
            await self._emit(FailureEvent(
                failure_type=FailureType.EVENT_LOOP_SATURATION,
                severity=FailureSeverity.CRITICAL,
                message=f"Event loop lag {h.event_loop_lag_ms:.0f}ms",
                context={"lag_ms": h.event_loop_lag_ms},
            ))
        elif h.event_loop_lag_ms > t.event_loop_lag_warning_ms:
            await self._emit(FailureEvent(
                failure_type=FailureType.EVENT_LOOP_SATURATION,
                severity=FailureSeverity.WARNING,
                message=f"Event loop lag elevated: {h.event_loop_lag_ms:.0f}ms",
                context={"lag_ms": h.event_loop_lag_ms},
            ))

        # ── 7. Throughput collapse ───────────────────────────────────
        if m.speed_ema.is_valid and m.speed_buffer.count >= 10:
            rolling_mean = m.speed_buffer.mean()
            if rolling_mean > 0:
                ratio = m.speed_ema.value / rolling_mean
                if ratio < t.throughput_collapse_ratio:
                    await self._emit(FailureEvent(
                        failure_type=FailureType.THROUGHPUT_COLLAPSE,
                        severity=FailureSeverity.DEGRADED,
                        message=f"Throughput at {ratio:.0%} of rolling average",
                        context={
                            "current": m.speed_ema.value,
                            "rolling_mean": rolling_mean,
                            "ratio": ratio,
                        },
                    ))

    async def _emit(self, event: FailureEvent):
        self._events.append(event)
        # Keep bounded
        if len(self._events) > 200:
            self._events = self._events[-100:]
        logger.warning(
            "Failure detected",
            type=event.failure_type.value,
            severity=event.severity.value,
            message=event.message,
        )
        if self._on_failure:
            try:
                await self._on_failure(event)
            except Exception as e:
                logger.error("Failure handler error", error=str(e))
