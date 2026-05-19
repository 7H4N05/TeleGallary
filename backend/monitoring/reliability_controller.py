"""
Reliability Controller — top-level coordinator for the self-healing subsystem.

Wires together:
- HealthMonitor (system metrics)
- UploadMetrics (upload performance)
- ETAAnalyzer (accurate ETA)
- FailureDetector (anomaly detection)
- AdaptiveController (AIMD tuning)
- RecoveryEngine (autonomous recovery)
- MetricsStore (persistence)

Lifecycle:
    controller = ReliabilityController(session_id)
    await controller.start()
    ... uploads run ...
    await controller.stop()

The controller also drives periodic metrics persistence and SSE broadcasts
of the performance dashboard data.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from monitoring.metrics import UploadMetrics, get_upload_metrics
from monitoring.eta import ETAAnalyzer, get_eta_analyzer
from monitoring.metrics_store import MetricsStore, get_metrics_store
from monitoring.health_monitor import HealthMonitor, get_health_monitor
from monitoring.failure_detector import FailureDetector
from monitoring.adaptive_controller import AdaptiveController
from monitoring.recovery_engine import RecoveryEngine
from utils.logger import get_logger

logger = get_logger(__name__)


class ReliabilityController:
    """Top-level self-healing agent.

    NOT a chatbot — this is a fully autonomous background system that
    continuously monitors upload health and takes corrective action.
    """

    def __init__(
        self,
        session_id: str,
        event_emitter: Optional[Callable] = None,
        metrics_persist_interval: float = 30.0,
        dashboard_broadcast_interval: float = 5.0,
    ):
        self.session_id = session_id
        self._event_emitter = event_emitter

        # ── Subsystems ───────────────────────────────────────────────
        self.metrics: UploadMetrics = get_upload_metrics()
        self.eta: ETAAnalyzer = get_eta_analyzer()
        self.health: HealthMonitor = get_health_monitor()
        self.store: MetricsStore = get_metrics_store()
        self.detector: FailureDetector = FailureDetector(
            metrics=self.metrics,
            health=self.health,
        )
        self.adaptive: AdaptiveController = AdaptiveController(
            metrics=self.metrics,
        )
        self.recovery: RecoveryEngine = RecoveryEngine()

        # Wire detector → recovery engine + adaptive controller
        self.detector.set_failure_handler(self._on_failure)

        # Intervals
        self._persist_interval = metrics_persist_interval
        self._broadcast_interval = dashboard_broadcast_interval

        # Background tasks
        self._persist_task: Optional[asyncio.Task] = None
        self._broadcast_task: Optional[asyncio.Task] = None
        self._running = False

    async def _on_failure(self, event):
        """Fan out failure events to both recovery and adaptive controller."""
        await self.recovery.handle_failure(event)
        await self.adaptive.on_failure_event(event)

    def register_recovery_hooks(self, **hooks):
        """Pass recovery callbacks from the orchestrator."""
        self.recovery.register_hooks(**hooks)

    async def start(self):
        """Start all monitoring subsystems."""
        if self._running:
            return
        self._running = True

        self.metrics.reset()
        self.metrics.session_started_at = time.monotonic()
        self.eta.set_session_start(time.monotonic())

        await self.health.start(interval=10.0)
        await self.detector.start()
        await self.adaptive.start()

        self._persist_task = asyncio.create_task(self._persist_loop())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

        logger.info(
            "Reliability controller started",
            session_id=self.session_id,
        )

    async def stop(self):
        """Gracefully stop all subsystems."""
        self._running = False

        for task in [self._persist_task, self._broadcast_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await self.detector.stop()
        await self.adaptive.stop()
        await self.health.stop()

        # Final persist
        try:
            await self._persist_snapshot()
        except Exception:
            pass

        logger.info(
            "Reliability controller stopped",
            session_id=self.session_id,
        )

    # ── Periodic loops ───────────────────────────────────────────────

    async def _persist_loop(self):
        """Periodically save metrics to SQLite."""
        while self._running:
            try:
                await self._persist_snapshot()
            except Exception as e:
                logger.warning("Metrics persist failed", error=str(e))
            await asyncio.sleep(self._persist_interval)

    async def _persist_snapshot(self):
        snap = self.metrics.snapshot()
        eta_result = self.eta.compute(self.metrics)
        await self.store.save_snapshot(
            session_id=self.session_id,
            snapshot=snap,
            eta_seconds=eta_result.eta_seconds,
        )

    async def _broadcast_loop(self):
        """Periodically emit dashboard update events via SSE."""
        while self._running:
            try:
                if self._event_emitter:
                    snap = self.metrics.snapshot()
                    eta_result = self.eta.compute(self.metrics)
                    health_snap = self.health.snapshot_dict()

                    await self._event_emitter("dashboard", {
                        "session_id": self.session_id,
                        # ETA
                        "eta_seconds": eta_result.eta_seconds,
                        "eta_human": eta_result.eta_human,
                        "speed_files_per_sec": eta_result.speed_files_per_sec,
                        "throughput_mbps": eta_result.throughput_mbps,
                        "confidence_low_sec": eta_result.confidence_low_sec,
                        "confidence_high_sec": eta_result.confidence_high_sec,
                        "bottleneck": eta_result.bottleneck,
                        "is_stalled": eta_result.is_stalled,
                        # Progress
                        "uploaded_files": snap["uploaded_files"],
                        "total_files": snap["total_files"],
                        "remaining_files": snap["remaining_files"],
                        "completion_pct": snap["completion_pct"],
                        "elapsed_seconds": snap["elapsed_seconds"],
                        # Metrics
                        "speed_ema": snap["speed_files_per_sec"],
                        "throughput_ema": snap["throughput_bytes_per_sec"],
                        "preprocess_avg": snap["preprocess_avg_sec"],
                        "network_avg": snap["network_avg_sec"],
                        # Counters
                        "total_retries": snap["total_retries"],
                        "floodwait_count": snap["floodwait_count"],
                        "reconnect_count": snap["reconnect_count"],
                        "failed_uploads": snap["failed_uploads"],
                        # Health
                        "cpu_percent": health_snap["cpu_percent"],
                        "memory_rss_mb": health_snap["memory_rss_mb"],
                        "memory_trend": health_snap["memory_trend"],
                        "event_loop_lag_ms": health_snap["event_loop_lag_ms"],
                        "is_healthy": health_snap["is_healthy"],
                        "health_issues": health_snap["issues"],
                        # Adaptive params
                        "adaptive": self.adaptive.snapshot(),
                        # Recovery
                        "recovery": self.recovery.snapshot(),
                    })
            except Exception as e:
                logger.warning("Dashboard broadcast failed", error=str(e))
            await asyncio.sleep(self._broadcast_interval)

    # ── Convenience for orchestrator integration ─────────────────────

    def on_upload_success(self):
        self.adaptive.on_upload_success()

    def on_upload_failure(self):
        self.adaptive.on_upload_failure()

    def on_floodwait(self, seconds: float):
        self.metrics.record_floodwait(seconds)
        self.eta.record_floodwait(seconds)
        self.detector.record_floodwait()
        self.adaptive.on_floodwait(seconds)

    def on_reconnect(self):
        self.metrics.record_reconnect()
        self.detector.record_reconnect()

    def get_dashboard(self) -> dict:
        """Full dashboard snapshot for API endpoint."""
        snap = self.metrics.snapshot()
        eta_result = self.eta.compute(self.metrics)
        return {
            "metrics": snap,
            "eta": {
                "eta_seconds": eta_result.eta_seconds,
                "eta_human": eta_result.eta_human,
                "speed_files_per_sec": eta_result.speed_files_per_sec,
                "throughput_mbps": eta_result.throughput_mbps,
                "bottleneck": eta_result.bottleneck,
                "is_stalled": eta_result.is_stalled,
            },
            "health": self.health.snapshot_dict(),
            "adaptive": self.adaptive.snapshot(),
            "recovery": self.recovery.snapshot(),
            "failure_events": [
                {
                    "type": e.failure_type.value,
                    "severity": e.severity.value,
                    "message": e.message,
                    "timestamp": e.timestamp,
                }
                for e in self.detector.recent_events[-20:]
            ],
        }


# ── Global singleton ────────────────────────────────────────────────────
_controller: Optional[ReliabilityController] = None


def get_reliability_controller() -> Optional[ReliabilityController]:
    return _controller


def set_reliability_controller(ctrl: ReliabilityController):
    global _controller
    _controller = ctrl
