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
from telegram.peer_utils import resolve_upload_channel
from utils.logger import get_logger
from utils.session_crypto import maybe_decrypt_session

logger = get_logger(__name__)

# Active orchestrators keyed by session_id
_active_sessions: Dict[str, UploadOrchestrator] = {}


async def _validate_channel_for_account(account_id: str, channel_id: str) -> str:
    """Connect client, resolve channel, return canonical peer id."""
    async with AsyncSessionLocal() as db:
        account = await AccountRepository(db).get_by_id(account_id)
    if not account:
        raise ValueError(f"Account not found: {account_id}")

    client_mgr = get_client_manager()
    await client_mgr.add_client(
        account_id=account.id,
        phone=account.phone,
        api_id=account.api_id,
        api_hash=account.api_hash,
        session_string=maybe_decrypt_session(account.session_string),
    )
    if not await client_mgr.start_client(account_id):
        raise RuntimeError(f"Could not connect Telegram client for account {account_id}")

    client = await client_mgr.get_client(account_id)
    if not client:
        raise RuntimeError(f"Telegram client not available for account {account_id}")

    return await resolve_upload_channel(client, channel_id)


async def start_session(
    root_folder: str,
    channel_id: str,
    account_id: str,
    session_name: Optional[str] = None,
) -> str:
    """Create and start a new upload session. Returns session_id."""
    running = [sid for sid, o in _active_sessions.items() if o.is_running]
    if running:
        raise ValueError(
            "An upload is already running. Stop or pause it before starting a new session."
        )

    async with AsyncSessionLocal() as db:
        session_repo = SessionRepository(db)
        account_repo = AccountRepository(db)

        # Validate account exists
        account = await account_repo.get_by_id(account_id)
        if not account:
            raise ValueError(f"Account not found: {account_id}")

    resolved_channel_id = await _validate_channel_for_account(account_id, channel_id)

    async with AsyncSessionLocal() as db:
        session_repo = SessionRepository(db)
        name = session_name or f"Upload {root_folder}"
        session = await session_repo.create(
            {
                "name": name,
                "root_folder": root_folder,
                "channel_id": resolved_channel_id,
                "account_id": account_id,
            }
        )
        await db.commit()
        session_id = session.id

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
            stored_channel = s.channel_id

        try:
            resolved = await _validate_channel_for_account(acct_id, stored_channel)
        except ValueError as e:
            logger.error("Channel validation failed on resume", error=str(e))
            raise ValueError(str(e)) from e

        if resolved != stored_channel:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import update
                from db.models import UploadSession

                await db.execute(
                    update(UploadSession)
                    .where(UploadSession.id == session_id)
                    .values(channel_id=resolved)
                )
                await db.commit()

        orch = UploadOrchestrator(session_id=session_id)
        _active_sessions[session_id] = orch
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
