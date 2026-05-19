"""
Adaptive Controller — TCP-style AIMD concurrency and dynamic tuning.

Implements Additive-Increase / Multiplicative-Decrease (AIMD) congestion
control adapted for Telegram upload pipelines:

- **Additive Increase**: When uploads succeed consecutively, slowly increase
  concurrency / batch size / compression quality.
- **Multiplicative Decrease**: On FloodWait, retry storm, or stall, halve the
  aggressive parameters immediately.

Also dynamically tunes:
- Worker count
- Album batch size
- JPEG compression quality
- Retry intervals
- Inter-upload delay (pacing)

The controller runs as a background coroutine that checks metrics every
`adjust_interval_seconds` and applies gradual parameter changes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from monitoring.failure_detector import FailureEvent, FailureSeverity, FailureType
from monitoring.metrics import UploadMetrics
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AdaptiveParams:
    """The live-tunable parameters that the controller adjusts."""
    # Concurrency
    concurrent_uploads: int = 1
    max_concurrent_uploads: int = 4
    min_concurrent_uploads: int = 1

    # Batching
    album_size: int = 5
    max_album_size: int = 10
    min_album_size: int = 1

    # Quality
    jpeg_quality: int = 85
    max_jpeg_quality: int = 95
    min_jpeg_quality: int = 60

    # Retry
    retry_base_delay: float = 5.0
    retry_max_delay: float = 300.0

    # Pacing — inter-upload delay to avoid hammering Telegram
    pacing_delay_seconds: float = 0.0
    max_pacing_delay: float = 10.0

    # Internal AIMD state
    success_streak: int = 0
    increase_threshold: int = 20   # consecutive successes before increase
    decrease_factor: float = 0.5


class AdaptiveController:
    """AIMD-based adaptive parameter tuner.

    Wire it to:
    - `UploadMetrics` for speed/throughput readings
    - `FailureDetector` via `on_failure()` for immediate decrease events
    """

    def __init__(
        self,
        metrics: UploadMetrics,
        adjust_interval: float = 30.0,
    ):
        self._metrics = metrics
        self._params = AdaptiveParams()
        self._adjust_interval = adjust_interval
        self._task: Optional[asyncio.Task] = None
        self._last_floodwait_at: float = 0.0
        self._running = False

    @property
    def params(self) -> AdaptiveParams:
        return self._params

    def configure_limits(
        self,
        max_concurrent: int = 4,
        min_concurrent: int = 1,
        max_album: int = 10,
        min_album: int = 1,
    ):
        self._params.max_concurrent_uploads = max_concurrent
        self._params.min_concurrent_uploads = min_concurrent
        self._params.max_album_size = max_album
        self._params.min_album_size = min_album

    async def start(self):
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Adaptive controller started", interval=self._adjust_interval)

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
                self._periodic_adjust()
            except Exception as e:
                logger.warning("Adaptive controller error", error=str(e))
            await asyncio.sleep(self._adjust_interval)

    def on_upload_success(self):
        """Called after every successful upload to track streaks."""
        p = self._params
        p.success_streak += 1
        if p.success_streak >= p.increase_threshold:
            self._additive_increase()
            p.success_streak = 0

    def on_upload_failure(self):
        """Called on upload failure (non-FloodWait)."""
        self._params.success_streak = 0

    async def on_failure_event(self, event: FailureEvent):
        """Called by FailureDetector when a failure is detected."""
        if event.failure_type == FailureType.FLOODWAIT_ESCALATION:
            self._multiplicative_decrease("FloodWait escalation")
            self._increase_pacing(2.0)
        elif event.failure_type == FailureType.RETRY_STORM:
            self._multiplicative_decrease("Retry storm")
            self._increase_pacing(1.0)
        elif event.failure_type == FailureType.UPLOAD_STALL:
            # Don't decrease — stall might be network, not our fault
            self._increase_pacing(0.5)
        elif event.failure_type == FailureType.THROUGHPUT_COLLAPSE:
            self._multiplicative_decrease("Throughput collapse")
        elif event.failure_type == FailureType.MEMORY_PRESSURE:
            self._reduce_quality()
        elif event.failure_type == FailureType.EVENT_LOOP_SATURATION:
            self._reduce_concurrency(reason="Event loop saturated")

    def on_floodwait(self, wait_seconds: float):
        """Called on each FloodWait for immediate response."""
        self._params.success_streak = 0
        now = time.monotonic()

        # If we got a FloodWait within 5 min of the last one, aggressively back off
        if now - self._last_floodwait_at < 300:
            self._multiplicative_decrease("Rapid FloodWait")
            self._increase_pacing(3.0)
        else:
            self._increase_pacing(1.0)

        self._last_floodwait_at = now

    # ── AIMD mechanics ───────────────────────────────────────────────

    def _additive_increase(self):
        """Slowly increase performance parameters."""
        p = self._params
        changed = False

        # Increase concurrent uploads by 1
        if p.concurrent_uploads < p.max_concurrent_uploads:
            p.concurrent_uploads += 1
            changed = True

        # Increase album size by 1
        if p.album_size < p.max_album_size:
            p.album_size += 1
            changed = True

        # Decrease pacing
        if p.pacing_delay_seconds > 0:
            p.pacing_delay_seconds = max(0, p.pacing_delay_seconds - 0.5)
            changed = True

        if changed:
            logger.info(
                "AIMD additive increase",
                concurrent=p.concurrent_uploads,
                album_size=p.album_size,
                pacing=p.pacing_delay_seconds,
            )

    def _multiplicative_decrease(self, reason: str):
        """Aggressively reduce performance parameters."""
        p = self._params
        f = p.decrease_factor

        old_concurrent = p.concurrent_uploads
        old_album = p.album_size

        p.concurrent_uploads = max(
            p.min_concurrent_uploads,
            int(p.concurrent_uploads * f),
        )
        p.album_size = max(
            p.min_album_size,
            int(p.album_size * f),
        )
        p.success_streak = 0

        logger.warning(
            "AIMD multiplicative decrease",
            reason=reason,
            concurrent=f"{old_concurrent} → {p.concurrent_uploads}",
            album_size=f"{old_album} → {p.album_size}",
        )

    def _increase_pacing(self, increment: float):
        p = self._params
        p.pacing_delay_seconds = min(
            p.max_pacing_delay,
            p.pacing_delay_seconds + increment,
        )
        logger.info("Pacing delay increased", delay=p.pacing_delay_seconds)

    def _reduce_concurrency(self, reason: str):
        p = self._params
        if p.concurrent_uploads > p.min_concurrent_uploads:
            p.concurrent_uploads = max(
                p.min_concurrent_uploads,
                p.concurrent_uploads - 1,
            )
            logger.info("Concurrency reduced", reason=reason, concurrent=p.concurrent_uploads)

    def _reduce_quality(self):
        p = self._params
        if p.jpeg_quality > p.min_jpeg_quality:
            p.jpeg_quality = max(p.min_jpeg_quality, p.jpeg_quality - 5)
            logger.info("JPEG quality reduced for memory", quality=p.jpeg_quality)

    def _periodic_adjust(self):
        """Gentle adjustments based on metrics trends."""
        m = self._metrics
        p = self._params

        # If current speed is significantly better than usual, try increasing
        if (
            m.speed_ema.is_valid
            and m.speed_buffer.count >= 20
            and m.speed_ema.value > m.speed_buffer.percentile(75)
            and p.success_streak >= 10
        ):
            # Speed is above p75 — conditions are good
            if p.pacing_delay_seconds > 0:
                p.pacing_delay_seconds = max(0, p.pacing_delay_seconds - 0.2)

    def snapshot(self) -> dict:
        p = self._params
        return {
            "concurrent_uploads": p.concurrent_uploads,
            "album_size": p.album_size,
            "jpeg_quality": p.jpeg_quality,
            "pacing_delay_seconds": round(p.pacing_delay_seconds, 2),
            "retry_base_delay": p.retry_base_delay,
            "success_streak": p.success_streak,
        }
