"""
Upload Service — bridges API layer to the upload engine.
Manages the lifecycle of UploadOrchestrator instances.
"""

import asyncio
from typing import Dict, Optional

from db.database import AsyncSessionLocal
from db.models import SessionStatus
from db.repositories.account_repo import AccountRepository
from db.repositories.session_repo import SessionRepository
from engine.orchestrator import UploadOrchestrator
from telegram.client_manager import get_client_manager
from utils.logger import get_logger

logger = get_logger(__name__)

# Active orchestrators keyed by session_id
_active_sessions: Dict[str, UploadOrchestrator] = {}


async def start_session(
    root_folder: str,
    channel_id: str,
    account_id: str,
    session_name: Optional[str] = None,
) -> str:
    """Create and start a new upload session. Returns session_id."""
    async with AsyncSessionLocal() as db:
        session_repo = SessionRepository(db)
        account_repo = AccountRepository(db)

        # Validate account exists
        account = await account_repo.get_by_id(account_id)
        if not account:
            raise ValueError(f"Account not found: {account_id}")

        # Create session record
        name = session_name or f"Upload {root_folder}"
        session = await session_repo.create(
            {
                "name": name,
                "root_folder": root_folder,
                "channel_id": channel_id,
                "account_id": account_id,
            }
        )
        await db.commit()
        session_id = session.id

    # Ensure client is started
    client_mgr = get_client_manager()
    started = await client_mgr.start_client(account_id)
    if not started:
        raise RuntimeError(f"Could not connect Telegram client for account {account_id}")

    orchestrator = UploadOrchestrator(session_id=session_id)
    _active_sessions[session_id] = orchestrator
    orchestrator.start()

    logger.info("Session started", session_id=session_id, root_folder=root_folder)
    return session_id


async def pause_session(session_id: str) -> bool:
    orch = _active_sessions.get(session_id)
    if not orch:
        return False
    orch.pause()
    async with AsyncSessionLocal() as db:
        await SessionRepository(db).set_status(session_id, SessionStatus.paused)
        await db.commit()
    return True


async def resume_session(session_id: str) -> bool:
    orch = _active_sessions.get(session_id)
    if not orch:
        # Session may need re-attaching after restart
        async with AsyncSessionLocal() as db:
            sr = SessionRepository(db)
            s = await sr.get_by_id(session_id)
            if not s or not s.account_id:
                return False
            acct_id = s.account_id
        orch = UploadOrchestrator(session_id=session_id)
        _active_sessions[session_id] = orch
        client_mgr = get_client_manager()
        await client_mgr.start_client(acct_id)
        orch.start()
    else:
        orch.resume()
    async with AsyncSessionLocal() as db:
        await SessionRepository(db).set_status(session_id, SessionStatus.running)
        await db.commit()
    return True


async def stop_session(session_id: str) -> bool:
    orch = _active_sessions.get(session_id)
    if not orch:
        return False
    orch.stop()
    _active_sessions.pop(session_id, None)
    async with AsyncSessionLocal() as db:
        await SessionRepository(db).set_status(session_id, SessionStatus.stopped)
        await db.commit()
    return True


async def retry_failed(session_id: str, file_ids: list) -> int:
    """Reset failed files to pending and resume session."""
    from db.repositories.file_repo import FileRepository
    count = 0
    async with AsyncSessionLocal() as db:
        file_repo = FileRepository(db)
        for fid in file_ids:
            await file_repo.mark_for_retry(fid)
            count += 1
        await db.commit()
    await resume_session(session_id)
    return count


def get_active_sessions() -> Dict[str, UploadOrchestrator]:
    return _active_sessions
