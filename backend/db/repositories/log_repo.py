"""
Repository for Log and FailedUpload DB operations.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import FailedUpload, Log, LogLevel


class LogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def write(
        self,
        message: str,
        level: LogLevel = LogLevel.INFO,
        session_id: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> Log:
        log = Log(
            session_id=session_id,
            level=level,
            message=message,
            context=context,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def list_recent(
        self,
        session_id: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> List[Log]:
        q = select(Log).order_by(Log.created_at.desc()).limit(limit).offset(offset)
        if session_id:
            q = q.where(Log.session_id == session_id)
        result = await self.db.execute(q)
        return list(reversed(result.scalars().all()))


class FailedUploadRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        file_id: str,
        session_id: str,
        error_type: str,
        error_message: str,
    ) -> FailedUpload:
        record = FailedUpload(
            file_id=file_id,
            session_id=session_id,
            error_type=error_type,
            error_message=error_message,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    async def list_by_session(self, session_id: str) -> List[FailedUpload]:
        result = await self.db.execute(
            select(FailedUpload)
            .where(FailedUpload.session_id == session_id)
            .order_by(FailedUpload.occurred_at.desc())
        )
        return list(result.scalars().all())

    async def mark_resolved(self, failed_id: str) -> None:
        await self.db.execute(
            update(FailedUpload)
            .where(FailedUpload.id == failed_id)
            .values(resolved=True)
        )
