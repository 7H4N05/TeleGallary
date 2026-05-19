"""
ETA Analyzer — accurate, adaptive estimated-time-of-arrival calculation.

Combines multiple signals (EMA speed, rolling window, per-phase bottleneck
analysis) to produce a stable, responsive ETA that:

1. Adapts quickly to network changes (via EMA weighting)
2. Avoids spikes/flapping (via smoothing and clamping)
3. Correctly accounts for FloodWait idle time
4. Provides confidence intervals using jitter tracking
5. Works correctly over 12–48 hour upload sessions

Architecture
============
The ETA is a *weighted blend* of three estimators:

- **EMA-based**: Fast-adapting, weight = 0.5
- **Rolling-window median**: Robust to outliers, weight = 0.3
- **Per-phase projection**: Sums bottleneck durations, weight = 0.2

The final ETA is smoothed with a separate EMA (α=0.15) to prevent visual jitter
in the frontend display.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from monitoring.metrics import UploadMetrics, EMATracker
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ETAResult:
    """Packaged ETA output for the event bus / API."""
    eta_seconds: Optional[float]          # best-estimate ETA in seconds
    eta_human: str                        # "2h 14m" style string
    speed_files_per_sec: float            # current EMA speed
    throughput_mbps: float                # current network throughput (MB/s)
    confidence_low_sec: Optional[float]   # optimistic bound
    confidence_high_sec: Optional[float]  # pessimistic bound
    bottleneck: str                       # which phase dominates
    is_stalled: bool


class ETAAnalyzer:
    """Fuses multiple speed signals into a single smooth ETA.

    Call `compute()` periodically (e.g. after every upload or every 5 seconds).
    The returned ETAResult is ready for SSE broadcast.
    """

    def __init__(self, smoothing_alpha: float = 0.15):
        self._output_smoother = EMATracker(alpha=smoothing_alpha)
        self._floodwait_total: float = 0.0
        self._session_started: float = 0.0

    def set_session_start(self, ts: float):
        self._session_started = ts

    def record_floodwait(self, seconds: float):
        """Exclude FloodWait time from throughput calculations."""
        self._floodwait_total += seconds

    def compute(self, metrics: UploadMetrics) -> ETAResult:
        remaining = metrics.total_files - metrics.uploaded_files
        if remaining <= 0:
            return ETAResult(
                eta_seconds=0.0,
                eta_human="Complete",
                speed_files_per_sec=metrics.speed_ema.value,
                throughput_mbps=metrics.throughput_ema.value / (1024 * 1024) if metrics.throughput_ema.is_valid else 0.0,
                confidence_low_sec=0.0,
                confidence_high_sec=0.0,
                bottleneck="none",
                is_stalled=False,
            )

        # ── Signal 1: EMA-based estimate ─────────────────────────────
        ema_eta = None
        if metrics.speed_ema.is_valid and metrics.speed_ema.value > 0:
            ema_eta = remaining / metrics.speed_ema.value

        # ── Signal 2: Rolling-window median ──────────────────────────
        rolling_eta = None
        median_speed = metrics.speed_buffer.median()
        if median_speed > 0:
            rolling_eta = remaining / median_speed

        # ── Signal 3: Per-phase projection ───────────────────────────
        phase_eta = None
        if (
            metrics.preprocess_ema.is_valid
            and metrics.network_ema.is_valid
        ):
            per_file = (
                metrics.preprocess_ema.value
                + metrics.disk_read_ema.value
                + metrics.network_ema.value
                + metrics.api_latency_ema.value
            )
            if per_file > 0:
                phase_eta = remaining * per_file

        # ── Weighted blend ───────────────────────────────────────────
        estimates = []
        weights = []
        if ema_eta is not None:
            estimates.append(ema_eta)
            weights.append(0.5)
        if rolling_eta is not None:
            estimates.append(rolling_eta)
            weights.append(0.3)
        if phase_eta is not None:
            estimates.append(phase_eta)
            weights.append(0.2)

        if not estimates:
            # Fallback: rough estimate from elapsed time
            elapsed = time.monotonic() - self._session_started if self._session_started else 0
            active_time = max(elapsed - self._floodwait_total, 1.0)
            if metrics.uploaded_files > 0:
                raw = (active_time / metrics.uploaded_files) * remaining
            else:
                raw = None

            return ETAResult(
                eta_seconds=raw,
                eta_human=self._format(raw),
                speed_files_per_sec=0.0,
                throughput_mbps=0.0,
                confidence_low_sec=None,
                confidence_high_sec=None,
                bottleneck="unknown",
                is_stalled=metrics.stall.stall_detected,
            )

        total_weight = sum(weights)
        blended = sum(e * w for e, w in zip(estimates, weights)) / total_weight

        # ── Smooth to prevent flapping ───────────────────────────────
        smoothed = self._output_smoother.update(blended)

        # Clamp: ETA should never go negative or be astronomically large
        smoothed = max(0.0, min(smoothed, 7 * 24 * 3600))  # cap at 7 days

        # ── Confidence interval from jitter ──────────────────────────
        conf_low = None
        conf_high = None
        if metrics.speed_ema.is_valid and metrics.speed_ema.value > 0:
            jitter_factor = metrics.speed_ema.jitter / metrics.speed_ema.value if metrics.speed_ema.value > 0 else 0
            conf_low = smoothed * (1 - min(jitter_factor, 0.5))
            conf_high = smoothed * (1 + min(jitter_factor, 0.5))

        # ── Bottleneck identification ────────────────────────────────
        bottleneck = self._identify_bottleneck(metrics)

        throughput_mbps = 0.0
        if metrics.throughput_ema.is_valid:
            throughput_mbps = metrics.throughput_ema.value / (1024 * 1024)

        return ETAResult(
            eta_seconds=round(smoothed, 1),
            eta_human=self._format(smoothed),
            speed_files_per_sec=round(metrics.speed_ema.value, 4) if metrics.speed_ema.is_valid else 0.0,
            throughput_mbps=round(throughput_mbps, 2),
            confidence_low_sec=round(conf_low, 1) if conf_low is not None else None,
            confidence_high_sec=round(conf_high, 1) if conf_high is not None else None,
            bottleneck=bottleneck,
            is_stalled=metrics.stall.stall_detected,
        )

    @staticmethod
    def _identify_bottleneck(metrics: UploadMetrics) -> str:
        """Return the name of the phase consuming the most time."""
        phases = {
            "preprocess": metrics.preprocess_ema.value if metrics.preprocess_ema.is_valid else 0,
            "disk_io": metrics.disk_read_ema.value if metrics.disk_read_ema.is_valid else 0,
            "network": metrics.network_ema.value if metrics.network_ema.is_valid else 0,
            "api_latency": metrics.api_latency_ema.value if metrics.api_latency_ema.is_valid else 0,
        }
        if not any(phases.values()):
            return "unknown"
        return max(phases, key=phases.get)

    @staticmethod
    def _format(seconds: Optional[float]) -> str:
        if seconds is None:
            return "Calculating…"
        if seconds <= 0:
            return "Complete"
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        if s < 3600:
            return f"{s // 60}m {s % 60}s"
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m"


# ── Global singleton ────────────────────────────────────────────────────
_analyzer: Optional[ETAAnalyzer] = None


def get_eta_analyzer() -> ETAAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ETAAnalyzer()
    return _analyzer
