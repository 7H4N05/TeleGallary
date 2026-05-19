"""
Upload Orchestrator — top-level coordinator for an entire upload session.

Responsibilities:
- Build the upload queue from scanned folders
- Persist all folder/file records to DB on first run
- Resume from DB state on restart
- Drive FolderProcessor for each folder sequentially
- Handle pause/stop signals
- Emit progress events to SSE bus
"""

import asyncio
import time
from pathlib import Path
from typing import Optional

from db.database import AsyncSessionLocal
from db.models import LogLevel, SessionStatus
from db.repositories.file_repo import FileRepository
from db.repositories.folder_repo import FolderRepository
from db.repositories.log_repo import LogRepository
from db.repositories.session_repo import SessionRepository
from db.repositories.account_repo import AccountRepository
from engine.album_batcher import assign_album_indices
from engine.folder_processor import FolderProcessor
from telegram.client_manager import get_client_manager
from telegram.peer_utils import resolve_upload_channel, restore_peer_in_session
from telegram.uploader import TelegramUploader
from utils.file_utils import get_folder_display_name, scan_folders
from utils.logger import get_logger
from config.settings import get_settings
from services.event_bus import get_event_bus

logger = get_logger(__name__)


class UploadOrchestrator:
    """
    One instance per active upload session.
    Created by UploadService.start_session().
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()   # set = paused
        self._task: Optional[asyncio.Task] = None
        self._event_bus = get_event_bus()
        self._start_time: Optional[float] = None
        self._uploaded_count = 0
        self._total_count = 0

    # ── Control API ──────────────────────────────────────────────────

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._run())
        return self._task

    def pause(self):
        self._pause_event.set()
        logger.info("Upload paused", session_id=self.session_id)

    def resume(self):
        self._pause_event.clear()
        logger.info("Upload resumed", session_id=self.session_id)

    def stop(self):
        self._stop_event.set()
        self._pause_event.clear()
        logger.info("Upload stopped", session_id=self.session_id)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ── Internal event emitter ────────────────────────────────────────

    async def _emit(self, event_type: str, payload: dict):
        payload["session_id"] = self.session_id
        if "uploaded_files" in payload:
            self._uploaded_count = int(payload["uploaded_files"])
        # Inject live stats
        elapsed = time.time() - self._start_time if self._start_time else 0
        if elapsed > 0 and self._uploaded_count > 0:
            speed = self._uploaded_count / elapsed
            remaining = self._total_count - self._uploaded_count
            payload.setdefault("speed_bps", speed)
            payload.setdefault("eta_seconds", remaining / speed if speed > 0 else None)
        payload.setdefault("uploaded_files", self._uploaded_count)
        payload.setdefault("total_files", self._total_count)
        await self._event_bus.emit(event_type, payload)

    # ── Main run loop ─────────────────────────────────────────────────

    async def _run(self):
        self._start_time = time.time()
        async with AsyncSessionLocal() as db:
            session_repo = SessionRepository(db)
            folder_repo = FolderRepository(db)
            file_repo = FileRepository(db)
            log_repo = LogRepository(db)
            account_repo = AccountRepository(db)

            session = await session_repo.get_by_id(self.session_id)
            if not session:
                logger.error("Session not found", session_id=self.session_id)
                return

            # ── Crash recovery: reset stuck-uploading files ──────────
            await file_repo.reset_uploading_to_pending(self.session_id)
            await db.commit()

            # ── Mark session running ─────────────────────────────────
            await session_repo.set_status(self.session_id, SessionStatus.running)
            await db.commit()

            # ── Get Telegram client pool ─────────────────────────────
            client_mgr = get_client_manager()
            if client_mgr.pick_managed_client(session.account_id) is None:
                logger.error(
                    "No Telegram client available — connect accounts first",
                    account_id=session.account_id,
                )
                await session_repo.set_status(self.session_id, SessionStatus.failed)
                await db.commit()
                return

            client = await client_mgr.get_client(session.account_id)
            if client:
                # ── Restore peer cache from stored access_hash ────────────
                # This is the authoritative fix for PEER_ID_INVALID after
                # reconnect.  MemoryStorage loses the access_hash on every
                # restart; restoring it here means all subsequent RPCs that
                # address the channel by numeric ID will succeed.
                if session.channel_access_hash:
                    await restore_peer_in_session(
                        client, session.channel_id, session.channel_access_hash
                    )
                else:
                    # No stored hash yet — resolve via get_chat() or get_dialogs()
                    # fallback.  This covers both fresh sessions and old session
                    # rows created before the access_hash column was added.
                    try:
                        from sqlalchemy import update
                        from db.models import UploadSession

                        resolved, new_hash = await resolve_upload_channel(
                            client, session.channel_id
                        )
                        if new_hash:
                            await db.execute(
                                update(UploadSession)
                                .where(UploadSession.id == self.session_id)
                                .values(
                                    channel_id=resolved,
                                    channel_access_hash=new_hash,
                                )
                            )
                            session.channel_id = resolved
                            # Update in-memory so TelegramUploader gets the hash
                            session.channel_access_hash = new_hash
                            await db.commit()
                            logger.info(
                                "Stored access_hash from live resolve",
                                session_id=self.session_id,
                            )
                        else:
                            logger.warning(
                                "Could not obtain access_hash — uploads will fail with "
                                "PEER_ID_INVALID. Open the channel in Telegram app with "
                                "this account so it appears in recent dialogs, then retry.",
                                session_id=self.session_id,
                                channel_id=session.channel_id,
                            )
                    except ValueError as e:
                        logger.error("Channel validation failed", error=str(e))
                        await log_repo.write(
                            f"[ERROR] Invalid channel: {e}",
                            level=LogLevel.ERROR,
                            session_id=self.session_id,
                        )
                        await session_repo.set_status(self.session_id, SessionStatus.failed)
                        await db.commit()
                        return

            uploader = TelegramUploader(
                preferred_account_id=session.account_id,
                event_emitter=self._emit,
                channel_access_hash=session.channel_access_hash,
            )


            # ── Build queue if first run ─────────────────────────────
            folders = await folder_repo.list_by_session(self.session_id)
            if not folders:
                folders = await self._build_queue(
                    session, db, folder_repo, file_repo, log_repo
                )
                if not folders:
                    await session_repo.set_status(self.session_id, SessionStatus.failed)
                    await db.commit()
                    return

            # ── Compute totals ───────────────────────────────────────
            await session_repo.sync_file_counts_from_db(self.session_id)
            session = await session_repo.get_by_id(self.session_id)
            if not session:
                return
            self._total_count = session.total_files
            self._uploaded_count = session.uploaded_files

            await log_repo.write(
                f"[INFO] Starting upload session — {len(folders)} folders, {self._total_count} photos",
                level=LogLevel.INFO,
                session_id=self.session_id,
            )
            await db.commit()

        # ── Process each folder ──────────────────────────────────────
        async with AsyncSessionLocal() as db:
            folder_repo = FolderRepository(db)
            pending_folders = await folder_repo.list_pending(self.session_id)

        for folder in pending_folders:
            if self._stop_event.is_set():
                break

            # Wait if paused
            while self._pause_event.is_set():
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(0.5)

            processor = FolderProcessor(
                folder_id=folder.id,
                session_id=self.session_id,
                channel_id=None,  # fetched inside processor
                uploader=uploader,
                event_emitter=self._emit,
                stop_event=self._stop_event,
                pause_event=self._pause_event,
            )

            # Inject channel_id
            async with AsyncSessionLocal() as db:
                sr = SessionRepository(db)
                s = await sr.get_by_id(self.session_id)
                processor.channel_id = s.channel_id

            success = await processor.process()

            async with AsyncSessionLocal() as db:
                sr = SessionRepository(db)
                await sr.sync_file_counts_from_db(self.session_id)
                sess = await sr.get_by_id(self.session_id)
                if sess:
                    self._uploaded_count = sess.uploaded_files
                await db.commit()

        # ── Finalize session ─────────────────────────────────────────
        async with AsyncSessionLocal() as db:
            session_repo = SessionRepository(db)
            log_repo = LogRepository(db)
            await session_repo.sync_file_counts_from_db(self.session_id)
            if self._stop_event.is_set():
                await session_repo.set_status(self.session_id, SessionStatus.stopped)
                await log_repo.write(
                    "[INFO] Upload session stopped by user",
                    level=LogLevel.INFO,
                    session_id=self.session_id,
                )
            else:
                await session_repo.set_status(self.session_id, SessionStatus.completed)
                await log_repo.write(
                    f"[INFO] Upload session completed — {self._uploaded_count}/{self._total_count} photos uploaded",
                    level=LogLevel.INFO,
                    session_id=self.session_id,
                )
            await db.commit()

        await self._emit("complete", {"message": "Upload session finished"})
        logger.info("Session finished", session_id=self.session_id)

    # ── Queue builder (first run only) ───────────────────────────────

    async def _build_queue(self, session, db, folder_repo, file_repo, log_repo):
        """Scan root folder and persist folder/file records to DB."""
        try:
            scanned = scan_folders(session.root_folder)
        except (FileNotFoundError, NotADirectoryError) as e:
            logger.error("Scan failed", error=str(e))
            await log_repo.write(
                f"[ERROR] Scan failed: {e}",
                level=LogLevel.ERROR,
                session_id=self.session_id,
            )
            await db.commit()
            return []

        total_files = 0
        folders_created = []

        for order_idx, (folder_path, photo_paths) in enumerate(scanned):
            display = get_folder_display_name(folder_path, session.root_folder)
            folder = await folder_repo.create(
                {
                    "session_id": self.session_id,
                    "path": folder_path,
                    "name": display,
                    "order_index": order_idx,
                    "total_files": len(photo_paths),
                }
            )
            await db.flush()

            # Create file records with album assignments
            file_records = []
            for path, order_index, album_index, album_position in assign_album_indices(
                photo_paths, get_settings().album_size
            ):
                p = Path(path)
                file_records.append(
                    {
                        "folder_id": folder.id,
                        "session_id": self.session_id,
                        "path": path,
                        "filename": p.name,
                        "size_bytes": p.stat().st_size if p.exists() else 0,
                        "order_index": order_index,
                        "album_index": album_index,
                        "album_position": album_position,
                    }
                )
            await file_repo.bulk_create(file_records)
            total_files += len(photo_paths)
            folders_created.append(folder)

        from sqlalchemy import update
        from db.models import UploadSession
        await db.execute(
            update(UploadSession)
            .where(UploadSession.id == self.session_id)
            .values(total_files=total_files)
        )
        self._total_count = total_files
        await db.commit()
        logger.info(
            "Queue built",
            folders=len(folders_created),
            total_files=total_files,
        )
        return folders_created
