"""
Metrics Store — persist upload metrics snapshots to SQLite.

Periodically saves a metrics snapshot row so that:
- The dashboard can show historical trends
- Recovery after crash can reload last-known metrics state
- Long-running sessions have an audit trail of performance

Uses a lightweight dedicated table to avoid bloating the main models.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal, Base
from utils.logger import get_logger

logger = get_logger(__name__)


class MetricsSnapshot(Base):
    """One row = one point-in-time metrics snapshot."""
    __tablename__ = "metrics_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True)
    captured_at = Column(DateTime, server_default=func.now(), nullable=False)
    elapsed_seconds = Column(Float, default=0.0)
    uploaded_files = Column(Integer, default=0)
    total_files = Column(Integer, default=0)
    speed_ema = Column(Float, nullable=True)
    throughput_ema = Column(Float, nullable=True)
    eta_seconds = Column(Float, nullable=True)
    floodwait_count = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    stall_detected = Column(Integer, default=0)
    snapshot_json = Column(Text, nullable=True)  # full snapshot for rich queries


class MetricsStore:
    """Async helper to persist and load metrics snapshots."""

    async def save_snapshot(
        self,
        session_id: str,
        snapshot: Dict,
        eta_seconds: Optional[float] = None,
    ):
        async with AsyncSessionLocal() as db:
            row = MetricsSnapshot(
                session_id=session_id,
                elapsed_seconds=snapshot.get("elapsed_seconds", 0),
                uploaded_files=snapshot.get("uploaded_files", 0),
                total_files=snapshot.get("total_files", 0),
                speed_ema=snapshot.get("speed_files_per_sec"),
                throughput_ema=snapshot.get("throughput_bytes_per_sec"),
                eta_seconds=eta_seconds,
                floodwait_count=snapshot.get("floodwait_count", 0),
                retry_count=snapshot.get("total_retries", 0),
                stall_detected=int(snapshot.get("stall_detected", False)),
                snapshot_json=json.dumps(snapshot, default=str),
            )
            db.add(row)
            await db.commit()

    async def get_recent(
        self, session_id: str, limit: int = 100
    ) -> List[Dict]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MetricsSnapshot)
                .where(MetricsSnapshot.session_id == session_id)
                .order_by(MetricsSnapshot.captured_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "captured_at": str(r.captured_at),
                    "elapsed_seconds": r.elapsed_seconds,
                    "uploaded_files": r.uploaded_files,
                    "total_files": r.total_files,
                    "speed_ema": r.speed_ema,
                    "throughput_ema": r.throughput_ema,
                    "eta_seconds": r.eta_seconds,
                    "floodwait_count": r.floodwait_count,
                    "retry_count": r.retry_count,
                    "stall_detected": bool(r.stall_detected),
                }
                for r in reversed(rows)  # oldest first for timeline
            ]

    async def cleanup_old(self, session_id: str, keep_last: int = 500):
        """Keep only the most recent N rows per session."""
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            # Find the Nth-from-last ID
            sub = (
                select(MetricsSnapshot.id)
                .where(MetricsSnapshot.session_id == session_id)
                .order_by(MetricsSnapshot.captured_at.desc())
                .offset(keep_last)
                .limit(1)
            )
            cutoff_row = await db.execute(sub)
            cutoff_id = cutoff_row.scalar_one_or_none()
            if cutoff_id is not None:
                await db.execute(
                    delete(MetricsSnapshot).where(
                        MetricsSnapshot.session_id == session_id,
                        MetricsSnapshot.id <= cutoff_id,
                    )
                )
                await db.commit()


# ── Global singleton ────────────────────────────────────────────────────
_store: Optional[MetricsStore] = None


def get_metrics_store() -> MetricsStore:
    global _store
    if _store is None:
        _store = MetricsStore()
    return _store
