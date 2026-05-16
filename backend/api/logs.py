"""
Logs API — paginated log retrieval and SSE log streaming.
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from db.database import AsyncSessionLocal
from db.repositories.log_repo import LogRepository
from models.schemas import LogOut

router = APIRouter()


@router.get("/", response_model=list[LogOut])
async def get_logs(session_id: str = None, limit: int = 200, offset: int = 0):
    async with AsyncSessionLocal() as db:
        repo = LogRepository(db)
        logs = await repo.list_recent(
            session_id=session_id, limit=limit, offset=offset
        )
        return [LogOut.model_validate(l) for l in logs]
