"""
Repository for File DB operations — the core of resume logic.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import File, FileStatus


class FileRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def bulk_create(self, files: List[dict]) -> None:
        objs = [File(**f) for f in files]
        self.db.add_all(objs)
        await self.db.flush()

    async def get_by_id(self, file_id: str) -> Optional[File]:
        result = await self.db.execute(select(File).where(File.id == file_id))
        return result.scalar_one_or_none()

    async def list_by_folder(self, folder_id: str) -> List[File]:
        result = await self.db.execute(
            select(File)
            .where(File.folder_id == folder_id)
            .order_by(File.order_index)
        )
        return list(result.scalars().all())

    async def list_pending_for_folder(self, folder_id: str) -> List[File]:
        """Return files not yet uploaded — used for resume."""
        result = await self.db.execute(
            select(File)
            .where(
                File.folder_id == folder_id,
                File.status.in_([FileStatus.pending, FileStatus.uploading]),
            )
            .order_by(File.order_index)
        )
        return list(result.scalars().all())

    async def list_failed_for_session(self, session_id: str) -> List[File]:
        result = await self.db.execute(
            select(File)
            .where(
                File.session_id == session_id,
                File.status == FileStatus.failed,
            )
            .order_by(File.folder_id, File.order_index)
        )
        return list(result.scalars().all())

    async def count_failed_for_session(self, session_id: str) -> int:
        result = await self.db.execute(
            select(func.count(File.id)).where(
                File.session_id == session_id,
                File.status == FileStatus.failed,
            )
        )
        return int(result.scalar_one() or 0)

    async def count_uploaded_for_session(self, session_id: str) -> int:
        result = await self.db.execute(
            select(func.count(File.id)).where(
                File.session_id == session_id,
                File.status == FileStatus.uploaded,
            )
        )
        return int(result.scalar_one() or 0)

    async def reset_uploading_to_pending(self, session_id: str) -> None:
        """
        On restart, any file stuck in 'uploading' state means a crash
        happened mid-upload. Reset to pending so they get re-sent.
        """
        await self.db.execute(
            update(File)
            .where(
                File.session_id == session_id,
                File.status == FileStatus.uploading,
            )
            .values(status=FileStatus.pending)
        )

    async def mark_uploading(self, file_id: str) -> None:
        await self.db.execute(
            update(File)
            .where(File.id == file_id)
            .values(status=FileStatus.uploading, attempts=File.attempts + 1)
        )

    async def mark_uploaded(self, file_id: str, message_id: int) -> None:
        await self.db.execute(
            update(File)
            .where(File.id == file_id)
            .values(
                status=FileStatus.uploaded,
                message_id=message_id,
                uploaded_at=datetime.utcnow(),
            )
        )

    async def mark_failed(self, file_id: str, error: str) -> None:
        await self.db.execute(
            update(File)
            .where(File.id == file_id)
            .values(status=FileStatus.failed, last_error=error)
        )

    async def mark_for_retry(self, file_id: str) -> None:
        await self.db.execute(
            update(File)
            .where(File.id == file_id)
            .values(status=FileStatus.pending, last_error=None)
        )
