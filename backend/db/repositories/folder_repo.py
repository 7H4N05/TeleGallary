"""
Repository for Folder DB operations.
"""

from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Folder, FolderStatus


class FolderRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict) -> Folder:
        folder = Folder(**data)
        self.db.add(folder)
        await self.db.flush()
        await self.db.refresh(folder)
        return folder

    async def get_by_id(self, folder_id: str) -> Optional[Folder]:
        result = await self.db.execute(
            select(Folder).where(Folder.id == folder_id)
        )
        return result.scalar_one_or_none()

    async def list_by_session(self, session_id: str) -> List[Folder]:
        result = await self.db.execute(
            select(Folder)
            .where(Folder.session_id == session_id)
            .order_by(Folder.order_index)
        )
        return list(result.scalars().all())

    async def list_pending(self, session_id: str) -> List[Folder]:
        result = await self.db.execute(
            select(Folder)
            .where(
                Folder.session_id == session_id,
                Folder.status != FolderStatus.completed,
            )
            .order_by(Folder.order_index)
        )
        return list(result.scalars().all())

    async def set_status(self, folder_id: str, status: FolderStatus) -> None:
        await self.db.execute(
            update(Folder).where(Folder.id == folder_id).values(status=status)
        )

    async def save_start_msg_id(self, folder_id: str, msg_id: int) -> None:
        await self.db.execute(
            update(Folder)
            .where(Folder.id == folder_id)
            .values(start_msg_id=msg_id, status=FolderStatus.started)
        )

    async def save_end_msg_id(self, folder_id: str, msg_id: int) -> None:
        await self.db.execute(
            update(Folder)
            .where(Folder.id == folder_id)
            .values(end_msg_id=msg_id, status=FolderStatus.completed)
        )

    async def increment_uploaded(self, folder_id: str) -> None:
        folder = await self.get_by_id(folder_id)
        if folder:
            await self.db.execute(
                update(Folder)
                .where(Folder.id == folder_id)
                .values(uploaded_files=folder.uploaded_files + 1)
            )
