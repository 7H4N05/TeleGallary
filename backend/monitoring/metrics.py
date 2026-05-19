"""
Upload Metrics — EMA speed tracker, rolling sample buffer, and per-phase
breakdown of upload performance.

This is the core measurement layer that feeds the ETA Analyzer, the Adaptive
Controller, and the Prometheus-compatible /metrics endpoint.

Key concepts
============
- **EMA (Exponential Moving Average)**: Gives recent observations higher weight
  so the metric tracks current conditions, not lifetime history.
- **Rolling window**: Keeps the last *N* observations so we can compute
  percentiles, jitter, and variance over a bounded period.
- **Per-phase timing**: Every upload is decomposed into *preprocess →
  disk-read → upload → api-latency* so we can identify which phase is
  the bottleneck.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# ─── EMA tracker ────────────────────────────────────────────────────────
class EMATracker:
    """Exponential Moving Average with configurable smoothing.

    α (alpha) determines how quickly the average adapts:
      - α = 0.1 → slow adaptation, very smooth
      - α = 0.3 → moderate (default for speed tracking)
      - α = 0.5 → fast adaptation, more jittery

    The tracker also maintains a *jitter* EMA (absolute deviation from the
    current average) which the ETA analyzer uses to compute confidence
    intervals.
    """

    __slots__ = ("alpha", "value", "jitter", "_initialized")

    def __init__(self, alpha: float = 0.3):
        self.alpha = alpha
        self.value: float = 0.0
        self.jitter: float = 0.0
        self._initialized = False

    def update(self, sample: float) -> float:
        if not self._initialized:
            self.value = sample
            self.jitter = 0.0
            self._initialized = True
        else:
            self.jitter = (1 - self.alpha) * self.jitter + self.alpha * abs(
                sample - self.value
            )
            self.value = (1 - self.alpha) * self.value + self.alpha * sample
        return self.value

    def reset(self):
        self.value = 0.0
        self.jitter = 0.0
        self._initialized = False

    @property
    def is_valid(self) -> bool:
        return self._initialized


# ─── Rolling sample buffer ──────────────────────────────────────────────
class RollingBuffer:
    """Fixed-size buffer of timestamped samples for windowed statistics.

    Provides: mean, median, p95, min, max over the last *maxlen* observations.
    Automatically discards samples older than *max_age_seconds*.
    """

    __slots__ = ("_buf", "_max_age")

    def __init__(self, maxlen: int = 200, max_age_seconds: float = 3600.0):
        self._buf: Deque[tuple[float, float]] = deque(maxlen=maxlen)
        self._max_age = max_age_seconds

    def push(self, value: float, ts: float | None = None):
        ts = ts or time.monotonic()
        self._buf.append((ts, value))

    def _fresh(self) -> List[float]:
        """Return values that are not stale."""
        cutoff = time.monotonic() - self._max_age
        return [v for t, v in self._buf if t >= cutoff]

    @property
    def count(self) -> int:
        return len(self._fresh())

    def mean(self) -> float:
        vals = self._fresh()
        return sum(vals) / len(vals) if vals else 0.0

    def median(self) -> float:
        vals = sorted(self._fresh())
        n = len(vals)
        if n == 0:
            return 0.0
        mid = n // 2
        if n % 2 == 0:
            return (vals[mid - 1] + vals[mid]) / 2
        return vals[mid]

    def percentile(self, p: float) -> float:
        vals = sorted(self._fresh())
        if not vals:
            return 0.0
        idx = int(len(vals) * p / 100)
        return vals[min(idx, len(vals) - 1)]

    def min(self) -> float:
        vals = self._fresh()
        return min(vals) if vals else 0.0

    def max(self) -> float:
        vals = self._fresh()
        return max(vals) if vals else 0.0

    def values(self) -> List[float]:
        return self._fresh()

    def clear(self):
        self._buf.clear()


# ─── Upload phase enum ──────────────────────────────────────────────────
class UploadPhase(str, Enum):
    PREPROCESS = "preprocess"     # PIL decode + resize + encode
    DISK_READ = "disk_read"       # reading file from disk
    NETWORK_UPLOAD = "network"    # Pyrogram send_photo / send_media_group
    API_LATENCY = "api_latency"   # Telegram RPC round-trip overhead


# ─── Single upload timing record ────────────────────────────────────────
@dataclass
class UploadTiming:
    file_id: str
    file_size_bytes: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0
    # per-phase durations in seconds
    preprocess_duration: float = 0.0
    disk_read_duration: float = 0.0
    network_duration: float = 0.0
    api_latency: float = 0.0
    success: bool = True
    error_type: str = ""
    retry_count: int = 0

    @property
    def total_duration(self) -> float:
        if self.completed_at and self.started_at:
            return self.completed_at - self.started_at
        return 0.0

    @property
    def throughput_bps(self) -> float:
        """Bytes per second based on network upload phase only."""
        if self.network_duration > 0 and self.file_size_bytes > 0:
            return self.file_size_bytes / self.network_duration
        return 0.0

    @property
    def files_per_second(self) -> float:
        """End-to-end file throughput."""
        dur = self.total_duration
        return 1.0 / dur if dur > 0 else 0.0


# ─── Stall detection state ──────────────────────────────────────────────
@dataclass
class StallState:
    """Tracks whether uploads appear stalled."""
    last_progress_at: float = field(default_factory=time.monotonic)
    last_uploaded_count: int = 0
    stall_detected: bool = False
    stall_duration: float = 0.0
    consecutive_stalls: int = 0


# ─── Upload Metrics (the singleton) ─────────────────────────────────────
class UploadMetrics:
    """Centralized metrics aggregator for the upload pipeline.

    Thread-safe. Designed to be updated from both the asyncio event loop and
    from the PIL-processing thread pool.

    Attributes stored:
    - EMA trackers for speed (files/s), throughput (bytes/s), per-phase times
    - Rolling buffers for the same quantities
    - Counters for retries, FloodWaits, errors, reconnects
    - Stall detection state
    """

    def __init__(
        self,
        ema_alpha: float = 0.3,
        rolling_window: int = 200,
        rolling_max_age: float = 3600.0,
    ):
        self._lock = threading.Lock()

        # ── EMA trackers ─────────────────────────────────────────────
        self.speed_ema = EMATracker(alpha=ema_alpha)        # files/sec
        self.throughput_ema = EMATracker(alpha=ema_alpha)    # bytes/sec
        self.preprocess_ema = EMATracker(alpha=ema_alpha)    # seconds
        self.disk_read_ema = EMATracker(alpha=ema_alpha)     # seconds
        self.network_ema = EMATracker(alpha=ema_alpha)       # seconds
        self.api_latency_ema = EMATracker(alpha=ema_alpha)   # seconds

        # ── Rolling buffers ──────────────────────────────────────────
        self.speed_buffer = RollingBuffer(rolling_window, rolling_max_age)
        self.throughput_buffer = RollingBuffer(rolling_window, rolling_max_age)
        self.preprocess_buffer = RollingBuffer(rolling_window, rolling_max_age)
        self.disk_read_buffer = RollingBuffer(rolling_window, rolling_max_age)
        self.network_buffer = RollingBuffer(rolling_window, rolling_max_age)
        self.api_latency_buffer = RollingBuffer(rolling_window, rolling_max_age)

        # ── Counters ─────────────────────────────────────────────────
        self.total_uploads: int = 0
        self.successful_uploads: int = 0
        self.failed_uploads: int = 0
        self.total_retries: int = 0
        self.floodwait_count: int = 0
        self.floodwait_total_seconds: float = 0.0
        self.reconnect_count: int = 0
        self.total_bytes_uploaded: int = 0

        # ── Stall detection ──────────────────────────────────────────
        self.stall = StallState()

        # ── Session timing ───────────────────────────────────────────
        self.session_started_at: float = 0.0
        self.total_files: int = 0
        self.uploaded_files: int = 0

        # ── Recent timings for the metrics API ───────────────────────
        self._recent_timings: Deque[UploadTiming] = deque(maxlen=50)

    # ── Recording methods ────────────────────────────────────────────

    def record_upload(self, timing: UploadTiming):
        """Record a completed upload (success or failure)."""
        with self._lock:
            self._recent_timings.append(timing)
            self.total_uploads += 1
            self.total_retries += timing.retry_count

            if timing.success:
                self.successful_uploads += 1
                self.uploaded_files += 1
                self.total_bytes_uploaded += timing.file_size_bytes

                # Update EMAs
                fps = timing.files_per_second
                if fps > 0:
                    self.speed_ema.update(fps)
                    self.speed_buffer.push(fps)

                bps = timing.throughput_bps
                if bps > 0:
                    self.throughput_ema.update(bps)
                    self.throughput_buffer.push(bps)

                if timing.preprocess_duration > 0:
                    self.preprocess_ema.update(timing.preprocess_duration)
                    self.preprocess_buffer.push(timing.preprocess_duration)

                if timing.disk_read_duration > 0:
                    self.disk_read_ema.update(timing.disk_read_duration)
                    self.disk_read_buffer.push(timing.disk_read_duration)

                if timing.network_duration > 0:
                    self.network_ema.update(timing.network_duration)
                    self.network_buffer.push(timing.network_duration)

                if timing.api_latency > 0:
                    self.api_latency_ema.update(timing.api_latency)
                    self.api_latency_buffer.push(timing.api_latency)

                # Reset stall detection on progress
                self.stall.last_progress_at = time.monotonic()
                self.stall.last_uploaded_count = self.uploaded_files
                self.stall.stall_detected = False
                self.stall.consecutive_stalls = 0
            else:
                self.failed_uploads += 1

    def record_floodwait(self, wait_seconds: float):
        with self._lock:
            self.floodwait_count += 1
            self.floodwait_total_seconds += wait_seconds

    def record_reconnect(self):
        with self._lock:
            self.reconnect_count += 1

    def update_progress(self, uploaded: int, total: int):
        """Called periodically to sync counters from the orchestrator."""
        with self._lock:
            self.uploaded_files = uploaded
            self.total_files = total
            if uploaded > self.stall.last_uploaded_count:
                self.stall.last_progress_at = time.monotonic()
                self.stall.last_uploaded_count = uploaded
                self.stall.stall_detected = False
                self.stall.consecutive_stalls = 0

    def check_stall(self, stall_threshold_seconds: float = 300.0) -> bool:
        """Return True if no upload progress has been made for threshold."""
        with self._lock:
            elapsed = time.monotonic() - self.stall.last_progress_at
            if elapsed >= stall_threshold_seconds and self.uploaded_files < self.total_files:
                self.stall.stall_detected = True
                self.stall.stall_duration = elapsed
                self.stall.consecutive_stalls += 1
                return True
            return False

    # ── Snapshot for API / dashboard ─────────────────────────────────

    def snapshot(self) -> Dict:
        """Return a JSON-serializable snapshot of all metrics."""
        with self._lock:
            elapsed = time.monotonic() - self.session_started_at if self.session_started_at else 0
            return {
                # Speed
                "speed_files_per_sec": round(self.speed_ema.value, 4) if self.speed_ema.is_valid else None,
                "speed_files_per_sec_jitter": round(self.speed_ema.jitter, 4),
                "throughput_bytes_per_sec": round(self.throughput_ema.value, 2) if self.throughput_ema.is_valid else None,
                "throughput_bytes_per_sec_jitter": round(self.throughput_ema.jitter, 2),
                # Per-phase (EMA, seconds)
                "preprocess_avg_sec": round(self.preprocess_ema.value, 3) if self.preprocess_ema.is_valid else None,
                "disk_read_avg_sec": round(self.disk_read_ema.value, 3) if self.disk_read_ema.is_valid else None,
                "network_avg_sec": round(self.network_ema.value, 3) if self.network_ema.is_valid else None,
                "api_latency_avg_sec": round(self.api_latency_ema.value, 3) if self.api_latency_ema.is_valid else None,
                # Rolling window stats
                "speed_rolling_mean": round(self.speed_buffer.mean(), 4),
                "speed_rolling_p50": round(self.speed_buffer.median(), 4),
                "speed_rolling_p95": round(self.speed_buffer.percentile(95), 4),
                "throughput_rolling_mean": round(self.throughput_buffer.mean(), 2),
                # Counters
                "total_uploads": self.total_uploads,
                "successful_uploads": self.successful_uploads,
                "failed_uploads": self.failed_uploads,
                "total_retries": self.total_retries,
                "floodwait_count": self.floodwait_count,
                "floodwait_total_seconds": round(self.floodwait_total_seconds, 1),
                "reconnect_count": self.reconnect_count,
                "total_bytes_uploaded": self.total_bytes_uploaded,
                # Progress
                "uploaded_files": self.uploaded_files,
                "total_files": self.total_files,
                "remaining_files": self.total_files - self.uploaded_files,
                "completion_pct": round(
                    100 * self.uploaded_files / self.total_files, 2
                ) if self.total_files > 0 else 0.0,
                "elapsed_seconds": round(elapsed, 1),
                # Stall
                "stall_detected": self.stall.stall_detected,
                "stall_duration_sec": round(self.stall.stall_duration, 1),
                "consecutive_stalls": self.stall.consecutive_stalls,
                # Recent samples count
                "rolling_sample_count": self.speed_buffer.count,
            }

    def prometheus_text(self) -> str:
        """Export metrics in Prometheus text exposition format."""
        snap = self.snapshot()
        lines = []

        def _gauge(name: str, help_text: str, value):
            if value is not None:
                lines.append(f"# HELP telegallery_{name} {help_text}")
                lines.append(f"# TYPE telegallery_{name} gauge")
                lines.append(f"telegallery_{name} {value}")

        def _counter(name: str, help_text: str, value):
            lines.append(f"# HELP telegallery_{name} {help_text}")
            lines.append(f"# TYPE telegallery_{name} counter")
            lines.append(f"telegallery_{name} {value}")

        _gauge("speed_files_per_sec", "EMA upload speed in files/sec", snap["speed_files_per_sec"])
        _gauge("throughput_bytes_per_sec", "EMA throughput in bytes/sec", snap["throughput_bytes_per_sec"])
        _gauge("preprocess_avg_sec", "EMA preprocess duration seconds", snap["preprocess_avg_sec"])
        _gauge("network_avg_sec", "EMA network upload duration seconds", snap["network_avg_sec"])
        _gauge("completion_pct", "Upload completion percentage", snap["completion_pct"])
        _gauge("remaining_files", "Files remaining to upload", snap["remaining_files"])
        _gauge("stall_detected", "Whether upload stall is detected (1=yes)", int(snap["stall_detected"]))
        _counter("total_uploads", "Total upload attempts", snap["total_uploads"])
        _counter("successful_uploads", "Successful uploads", snap["successful_uploads"])
        _counter("failed_uploads", "Failed uploads", snap["failed_uploads"])
        _counter("total_retries", "Total retry attempts", snap["total_retries"])
        _counter("floodwait_count", "Number of FloodWait events", snap["floodwait_count"])
        _counter("reconnect_count", "Number of client reconnections", snap["reconnect_count"])
        _counter("total_bytes_uploaded", "Total bytes uploaded", snap["total_bytes_uploaded"])

        return "\n".join(lines) + "\n"

    def reset(self):
        """Full reset for new session."""
        with self._lock:
            self.speed_ema.reset()
            self.throughput_ema.reset()
            self.preprocess_ema.reset()
            self.disk_read_ema.reset()
            self.network_ema.reset()
            self.api_latency_ema.reset()
            self.speed_buffer.clear()
            self.throughput_buffer.clear()
            self.preprocess_buffer.clear()
            self.disk_read_buffer.clear()
            self.network_buffer.clear()
            self.api_latency_buffer.clear()
            self.total_uploads = 0
            self.successful_uploads = 0
            self.failed_uploads = 0
            self.total_retries = 0
            self.floodwait_count = 0
            self.floodwait_total_seconds = 0.0
            self.reconnect_count = 0
            self.total_bytes_uploaded = 0
            self.stall = StallState()
            self.session_started_at = 0.0
            self.total_files = 0
            self.uploaded_files = 0
            self._recent_timings.clear()


# ── Global singleton ────────────────────────────────────────────────────
_metrics: Optional[UploadMetrics] = None


def get_upload_metrics() -> UploadMetrics:
    global _metrics
    if _metrics is None:
        _metrics = UploadMetrics()
    return _metrics
