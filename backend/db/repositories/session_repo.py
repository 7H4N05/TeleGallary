"""
Repository for UploadSession DB operations.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import File, FileStatus, SessionStatus, UploadSession


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict) -> UploadSession:
        session = UploadSession(**data)
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)
        return session

    async def get_by_id(self, session_id: str) -> Optional[UploadSession]:
        result = await self.db.execute(
            select(UploadSession)
            .where(UploadSession.id == session_id)
            .options(selectinload(UploadSession.folders))
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> List[UploadSession]:
        result = await self.db.execute(
            select(UploadSession).order_by(UploadSession.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_resumable(self) -> Optional[UploadSession]:
        """Find the most recent running or paused session."""
        result = await self.db.execute(
            select(UploadSession)
            .where(UploadSession.status.in_([SessionStatus.running, SessionStatus.paused]))
            .order_by(UploadSession.created_at.desc())
        )
        return result.scalar_one_or_none()

    async def set_status(self, session_id: str, status: SessionStatus) -> None:
        values = {"status": status}
        if status == SessionStatus.running:
            values["started_at"] = datetime.utcnow()
        elif status in (SessionStatus.completed, SessionStatus.failed, SessionStatus.stopped):
            values["completed_at"] = datetime.utcnow()
        await self.db.execute(
            update(UploadSession).where(UploadSession.id == session_id).values(**values)
        )

    async def update_counts(
        self,
        session_id: str,
        uploaded_files: int,
        failed_files: int,
        total_files: int,
    ) -> None:
        await self.db.execute(
            update(UploadSession)
            .where(UploadSession.id == session_id)
            .values(
                uploaded_files=uploaded_files,
                failed_files=failed_files,
                total_files=total_files,
            )
        )

    async def increment_uploaded(self, session_id: str) -> None:
        session = await self.get_by_id(session_id)
        if session:
            await self.db.execute(
                update(UploadSession)
                .where(UploadSession.id == session_id)
                .values(uploaded_files=session.uploaded_files + 1)
            )

    async def increment_failed(self, session_id: str) -> None:
        session = await self.get_by_id(session_id)
        if session:
            await self.db.execute(
                update(UploadSession)
                .where(UploadSession.id == session_id)
                .values(failed_files=session.failed_files + 1)
            )

    async def sync_file_counts_from_db(self, session_id: str) -> None:
        """Recompute uploaded_files / failed_files from the files table (source of truth)."""
        uploaded = await self.db.scalar(
            select(func.count(File.id)).where(
                File.session_id == session_id,
                File.status == FileStatus.uploaded,
            )
        )
        failed = await self.db.scalar(
            select(func.count(File.id)).where(
                File.session_id == session_id,
                File.status == FileStatus.failed,
            )
        )
        await self.db.execute(
            update(UploadSession)
            .where(UploadSession.id == session_id)
            .values(
                uploaded_files=int(uploaded or 0),
                failed_files=int(failed or 0),
            )
        )
