"""
Folders API — folder status and file listing per session.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from db.database import AsyncSessionLocal
from db.repositories.folder_repo import FolderRepository
from db.repositories.file_repo import FileRepository

router = APIRouter()


class FolderOut(BaseModel):
    id: str
    session_id: str
    path: str
    name: str
    order_index: int
    status: str
    start_msg_id: Optional[int]
    end_msg_id: Optional[int]
    total_files: int
    uploaded_files: int

    class Config:
        from_attributes = True


class FileOut(BaseModel):
    id: str
    filename: str
    path: str
    status: str
    album_index: int
    album_position: int
    order_index: int
    attempts: int
    last_error: Optional[str]
    message_id: Optional[int]

    class Config:
        from_attributes = True


@router.get("/{session_id}", response_model=List[FolderOut])
async def list_folders(session_id: str):
    async with AsyncSessionLocal() as db:
        repo = FolderRepository(db)
        folders = await repo.list_by_session(session_id)
        return [FolderOut.model_validate(f) for f in folders]


@router.get("/{folder_id}/files", response_model=List[FileOut])
async def list_files(folder_id: str):
    async with AsyncSessionLocal() as db:
        repo = FileRepository(db)
        files = await repo.list_by_folder(folder_id)
        return [FileOut.model_validate(f) for f in files]
