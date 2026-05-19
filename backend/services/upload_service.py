"""
Upload Service — bridges API layer to the upload engine.
Manages the lifecycle of UploadOrchestrator instances.
"""

import asyncio
from typing import Dict, Optional

from sqlalchemy import update

from db.database import AsyncSessionLocal
from db.models import SessionStatus, UploadSession
from db.repositories.account_repo import AccountRepository
from db.repositories.session_repo import SessionRepository
from engine.orchestrator import UploadOrchestrator
from telegram.client_manager import get_client_manager
from telegram.peer_utils import resolve_upload_channel, restore_peer_in_session
from utils.logger import get_logger
from utils.session_crypto import maybe_decrypt_session

logger = get_logger(__name__)

# Active orchestrators keyed by session_id
_active_sessions: Dict[str, UploadOrchestrator] = {}


async def _connect_account(account_id: str) -> None:
    """Ensure the Pyrogram client for this account is registered and connected."""
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


async def _resolve_and_store_access_hash(
    account_id: str,
    channel_id: str,
    session_id: Optional[str] = None,
) -> tuple[str, Optional[int]]:
    """
    Connect the client, resolve the channel, extract the access_hash, and
    optionally persist it back to the session row.

    Returns (canonical_peer_id, access_hash).
    """
    await _connect_account(account_id)

    client = await get_client_manager().get_client(account_id)
    if not client:
        raise RuntimeError(f"Telegram client not available for account {account_id}")

    peer_id, access_hash = await resolve_upload_channel(client, channel_id)

    if session_id and access_hash is not None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(UploadSession)
                .where(UploadSession.id == session_id)
                .values(channel_access_hash=access_hash)
            )
            await db.commit()
        logger.info(
            "Stored channel access_hash in session",
            session_id=session_id,
            peer_id=peer_id,
        )

    return peer_id, access_hash


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
        account = await AccountRepository(db).get_by_id(account_id)
        if not account:
            raise ValueError(f"Account not found: {account_id}")

    # Resolve channel — this also populates the peer cache in MemoryStorage
    resolved_channel_id, access_hash = await _resolve_and_store_access_hash(
        account_id, channel_id
    )

    async with AsyncSessionLocal() as db:
        session_repo = SessionRepository(db)
        name = session_name or f"Upload {root_folder}"
        session = await session_repo.create(
            {
                "name": name,
                "root_folder": root_folder,
                "channel_id": resolved_channel_id,
                "channel_access_hash": access_hash,
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
            s = await SessionRepository(db).get_by_id(session_id)
            if not s or not s.account_id:
                return False
            acct_id = s.account_id
            stored_channel = s.channel_id
            stored_hash = s.channel_access_hash

        # Ensure client is connected
        await _connect_account(acct_id)
        client = await get_client_manager().get_client(acct_id)

        # Restore peer cache from stored access_hash BEFORE any RPC that needs the peer
        if stored_hash is not None and client:
            await restore_peer_in_session(client, stored_channel, stored_hash)
        elif client:
            # No stored hash: try a fresh resolve (works for @username channels)
            try:
                new_peer_id, new_hash = await resolve_upload_channel(client, stored_channel)
                if new_hash is not None:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(UploadSession)
                            .where(UploadSession.id == session_id)
                            .values(
                                channel_id=new_peer_id,
                                channel_access_hash=new_hash,
                            )
                        )
                        await db.commit()
                    stored_channel = new_peer_id
                    stored_hash = new_hash
            except ValueError as e:
                logger.error("Channel re-validation failed on resume", error=str(e))
                raise ValueError(str(e)) from e

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
