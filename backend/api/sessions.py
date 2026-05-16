"""
Sessions history API endpoints.
"""

from fastapi import APIRouter, HTTPException

from db.database import AsyncSessionLocal
from db.repositories.session_repo import SessionRepository
from db.repositories.file_repo import FileRepository
from db.repositories.log_repo import FailedUploadRepository
from models.schemas import FailedFileOut, SessionOut

router = APIRouter()


@router.get("/", response_model=list[SessionOut])
async def list_sessions():
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        sessions = await repo.list_all()
        return [SessionOut.model_validate(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str):
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        session = await repo.get_by_id(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionOut.model_validate(session)


@router.get("/{session_id}/failed", response_model=list[FailedFileOut])
async def get_failed_uploads(session_id: str):
    async with AsyncSessionLocal() as db:
        failed_repo = FailedUploadRepository(db)
        file_repo = FileRepository(db)
        records = await failed_repo.list_by_session(session_id)
        result = []
        for r in records:
            f = await file_repo.get_by_id(r.file_id)
            result.append(
                FailedFileOut(
                    id=r.id,
                    file_id=r.file_id,
                    session_id=r.session_id,
                    filename=f.filename if f else "unknown",
                    path=f.path if f else "",
                    error_type=r.error_type,
                    error_message=r.error_message,
                    occurred_at=r.occurred_at,
                    resolved=r.resolved,
                )
            )
        return result
