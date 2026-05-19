"""
Metrics API — Prometheus-compatible metrics, health-check, and
real-time performance dashboard endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from monitoring.metrics import get_upload_metrics
from monitoring.health_monitor import get_health_monitor
from monitoring.reliability_controller import get_reliability_controller
from monitoring.metrics_store import get_metrics_store
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/prometheus", response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus text exposition endpoint.

    Scrape this with Prometheus / Grafana at /api/metrics/prometheus
    """
    metrics = get_upload_metrics()
    return metrics.prometheus_text()


@router.get("/dashboard")
async def metrics_dashboard():
    """Full real-time dashboard data.

    Returns everything the frontend needs to render the performance dashboard:
    - Upload metrics (EMA, rolling, counters)
    - ETA with confidence intervals
    - System health (CPU, RAM, event loop lag)
    - Adaptive controller state
    - Recovery engine history
    - Recent failure events
    """
    controller = get_reliability_controller()
    if controller is None:
        # No active session — return basic health only
        return {
            "active_session": False,
            "health": get_health_monitor().snapshot_dict(),
            "metrics": get_upload_metrics().snapshot(),
        }
    return {
        "active_session": True,
        **controller.get_dashboard(),
    }


@router.get("/health")
async def detailed_health():
    """Detailed system health — CPU, memory, event loop lag, DB latency.

    More detailed than /api/health (which is a basic liveness check).
    """
    return get_health_monitor().snapshot_dict()


@router.get("/history/{session_id}")
async def metrics_history(session_id: str, limit: int = 100):
    """Historical metrics snapshots for charting.

    Returns up to `limit` point-in-time snapshots for the given session,
    ordered oldest-first for timeline rendering.
    """
    store = get_metrics_store()
    rows = await store.get_recent(session_id, limit=limit)
    return {"session_id": session_id, "snapshots": rows}


@router.get("/snapshot")
async def current_snapshot():
    """Raw current metrics snapshot (no ETA computation)."""
    return get_upload_metrics().snapshot()
