"""
Upload API endpoints.
"""

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from db.database import AsyncSessionLocal
from db.repositories.session_repo import SessionRepository
from models.schemas import (
    FolderInfo,
    FolderScanResult,
    RetryFailedRequest,
    SessionOut,
    StartUploadRequest,
)
from services import upload_service
from services.event_bus import get_event_bus
from utils.file_utils import compute_total_size, get_folder_display_name, human_size, scan_folders

router = APIRouter()


@router.post("/start", response_model=dict)
async def start_upload(req: StartUploadRequest):
    """Start a new upload session."""
    try:
        session_id = await upload_service.start_session(
            root_folder=req.root_folder,
            channel_id=req.channel_id,
            account_id=req.account_id,
            session_name=req.session_name,
        )
        return {"session_id": session_id, "status": "started"}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/resumable", response_model=SessionOut | None)
async def get_resumable_session():
    """Most recent session that can be resumed (running or paused, e.g. after app restart)."""
    async with AsyncSessionLocal() as db:
        repo = SessionRepository(db)
        row = await repo.get_resumable()
        if not row:
            return None
        return SessionOut.model_validate(row)


@router.post("/{session_id}/pause")
async def pause_upload(session_id: str):
    ok = await upload_service.pause_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found or not running")
    return {"status": "paused"}


@router.post("/{session_id}/resume")
async def resume_upload(session_id: str):
    ok = await upload_service.resume_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "resumed"}


@router.post("/{session_id}/stop")
async def stop_upload(session_id: str):
    ok = await upload_service.stop_session(session_id)
    return {"status": "stopped" if ok else "not_found"}


@router.post("/{session_id}/retry")
async def retry_failed(session_id: str, req: RetryFailedRequest):
    count = await upload_service.retry_failed(session_id, req.file_ids)
    return {"retried": count}


@router.get("/events")
async def upload_events():
    """SSE endpoint for real-time progress events."""
    bus = get_event_bus()
    q = bus.subscribe()

    async def generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            bus.unsubscribe(q)

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.post("/scan", response_model=FolderScanResult)
async def scan_folder(body: dict):
    """Scan a directory and return folder tree with photo counts."""
    root = body.get("root_folder", "")
    if not root:
        raise HTTPException(status_code=400, detail="root_folder required")
    try:
        scanned = scan_folders(root)
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    folders = []
    all_paths = []
    for folder_path, photos in scanned:
        size = compute_total_size(photos)
        folders.append(
            FolderInfo(
                path=folder_path,
                name=get_folder_display_name(folder_path, root),
                photo_count=len(photos),
                size_bytes=size,
                size_human=human_size(size),
            )
        )
        all_paths.extend(photos)

    total_size = compute_total_size(all_paths)
    return FolderScanResult(
        root=root,
        folders=folders,
        total_files=len(all_paths),
        total_size_bytes=total_size,
        total_size_human=human_size(total_size),
    )
