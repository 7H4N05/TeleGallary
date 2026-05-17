"""
Folder Processor — handles upload of a single folder end-to-end.

Flow per folder:
1. Check if already completed (skip)
2. Send START marker if not already sent
3. Load pending files from DB
4. Batch into albums of 10
5. Upload each album
6. Track per-file success/failure
7. Send END marker
8. Mark folder complete
"""

import asyncio
from typing import Callable

from db.database import AsyncSessionLocal
from db.models import FileStatus, FolderStatus
from db.repositories.failed_repo import FailedUploadRepository
from db.repositories.file_repo import FileRepository
from db.repositories.folder_repo import FolderRepository
from db.repositories.log_repo import LogRepository
from db.models import LogLevel
from config.settings import get_settings
from telegram.uploader import TelegramUploader
from utils.logger import get_logger

logger = get_logger(__name__)


class FolderProcessor:
    def __init__(
        self,
        folder_id: str,
        session_id: str,
        channel_id: str,
        uploader: TelegramUploader,
        event_emitter: Callable,
        stop_event: asyncio.Event,
        pause_event: asyncio.Event,
    ):
        self.folder_id = folder_id
        self.session_id = session_id
        self.channel_id = channel_id
        self.uploader = uploader
        self.event_emitter = event_emitter
        self.stop_event = stop_event
        self.pause_event = pause_event

    async def _emit_progress(self, file_repo: FileRepository, **kwargs):
        failed_n = await file_repo.count_failed_for_session(self.session_id)
        uploaded_n = await file_repo.count_uploaded_for_session(self.session_id)
        await self.event_emitter(
            "progress",
            {
                "session_id": self.session_id,
                "failed_files": failed_n,
                "uploaded_files": uploaded_n,
                **kwargs,
            },
        )

    async def _wait_if_paused(self):
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                return
            await asyncio.sleep(0.5)

    async def process(self) -> bool:
        """
        Process this folder. Returns True if completed, False if stopped/failed.
        """
        async with AsyncSessionLocal() as db:
            folder_repo = FolderRepository(db)
            file_repo = FileRepository(db)
            log_repo = LogRepository(db)
            failed_repo = FailedUploadRepository(db)

            folder = await folder_repo.get_by_id(self.folder_id)
            if not folder:
                return False

            if folder.status == FolderStatus.completed:
                logger.info("Folder already completed, skipping", folder=folder.name)
                return True

            await log_repo.write(
                f"[INFO] Processing folder: {folder.name}",
                level=LogLevel.INFO,
                session_id=self.session_id,
            )
            await db.commit()

            # ── Step 1: Send START marker if not yet sent ──────────────
            if folder.start_msg_id is None:
                start_text = get_settings().folder_start_template.format(
                    folder_name=folder.name.upper()
                )
                try:
                    msg = await self.uploader.send_text(self.channel_id, start_text)
                    await folder_repo.save_start_msg_id(self.folder_id, msg.id)
                    await db.commit()
                    await self._emit_progress(
                        file_repo,
                        current_folder=folder.name,
                        message=f"Started folder: {folder.name}",
                    )
                    logger.info("Sent START marker", folder=folder.name, msg_id=msg.id)
                except Exception as e:
                    logger.error("Failed to send START marker", folder=folder.name, error=str(e))
                    await log_repo.write(
                        f"[ERROR] Failed START marker for {folder.name}: {e}",
                        level=LogLevel.ERROR,
                        session_id=self.session_id,
                    )
                    await db.commit()
                    return False

            # ── Step 2: Load pending files ─────────────────────────────
            pending_files = await file_repo.list_pending_for_folder(self.folder_id)
            if not pending_files:
                # All files already uploaded, just send END if missing
                await self._send_end_marker(folder, folder_repo, file_repo, log_repo, db)
                return True

            # Group into albums by album_index
            albums: dict[int, list] = {}
            for f in pending_files:
                albums.setdefault(f.album_index, []).append(f)
            sorted_albums = sorted(albums.items())

            # ── Step 3: Upload album by album ──────────────────────────
            for album_idx, album_files in sorted_albums:
                await self._wait_if_paused()
                if self.stop_event.is_set():
                    await db.commit()
                    return False

                file_paths = [f.path for f in album_files]
                file_ids = [f.id for f in album_files]

                await self._emit_progress(
                    file_repo,
                    current_folder=folder.name,
                    current_album=album_idx,
                    current_file=album_files[0].filename,
                )

                # Mark all as uploading
                for f in album_files:
                    await file_repo.mark_uploading(f.id)
                await db.commit()

                logger.info(
                    "Uploading album",
                    folder=folder.name,
                    album_idx=album_idx,
                    photos=len(album_files),
                )
                uploaded = await self._upload_album(
                    album_idx=album_idx,
                    album_files=album_files,
                    file_ids=file_ids,
                    file_paths=file_paths,
                    folder=folder,
                    file_repo=file_repo,
                    folder_repo=folder_repo,
                    failed_repo=failed_repo,
                    log_repo=log_repo,
                    db=db,
                )
                if not uploaded:
                    await self._emit_progress(
                        file_repo,
                        current_folder=folder.name,
                        current_album=album_idx,
                        message=f"Album {album_idx} failed ({len(album_files)} files)",
                    )

            # ── Step 4: Send END marker ────────────────────────────────
            await self._send_end_marker(folder, folder_repo, file_repo, log_repo, db)
            return True

    async def _upload_album(
        self,
        album_idx: int,
        album_files,
        file_ids,
        file_paths,
        folder,
        file_repo,
        folder_repo,
        failed_repo,
        log_repo,
        db,
    ) -> bool:
        """Upload an album; on failure retry photos individually. Returns True if any progress."""
        try:
            messages = await self.uploader.send_album(self.channel_id, file_paths)

            if messages and len(messages) == len(file_ids):
                for fid, msg in zip(file_ids, messages):
                    await file_repo.mark_uploaded(fid, msg.id)
                    await folder_repo.increment_uploaded(self.folder_id)
                await db.commit()
                await log_repo.write(
                    f"[INFO] Uploaded album {album_idx} in {folder.name} ({len(file_ids)} photos)",
                    level=LogLevel.INFO,
                    session_id=self.session_id,
                )
                await db.commit()
                await self._emit_progress(
                    file_repo,
                    current_folder=folder.name,
                    current_album=album_idx,
                    message=f"Album {album_idx} uploaded",
                )
                return True

            raise RuntimeError(
                f"Message count mismatch: got {len(messages) if messages else 0}, expected {len(file_ids)}"
            )
        except Exception as album_err:
            logger.warning(
                "Album batch failed, retrying photos individually",
                album_idx=album_idx,
                folder=folder.name,
                error=str(album_err),
            )
            return await self._upload_album_individually(
                album_idx=album_idx,
                album_files=album_files,
                file_ids=file_ids,
                file_paths=file_paths,
                folder=folder,
                file_repo=file_repo,
                folder_repo=folder_repo,
                failed_repo=failed_repo,
                log_repo=log_repo,
                db=db,
                album_err=album_err,
            )

    async def _upload_album_individually(
        self,
        album_idx: int,
        album_files,
        file_ids,
        file_paths,
        folder,
        file_repo,
        folder_repo,
        failed_repo,
        log_repo,
        db,
        album_err: Exception,
    ) -> bool:
        ok_count = 0
        for f, fid, path in zip(album_files, file_ids, file_paths):
            try:
                msg = await self.uploader.upload_photo_with_retry(self.channel_id, path)
                if msg:
                    await file_repo.mark_uploaded(fid, msg.id)
                    await folder_repo.increment_uploaded(self.folder_id)
                    ok_count += 1
                else:
                    raise RuntimeError("Upload returned no message")
            except Exception as e:
                err = str(e)
                await file_repo.mark_failed(f.id, err)
                await failed_repo.create(
                    file_id=f.id,
                    session_id=self.session_id,
                    error_type=type(e).__name__,
                    error_message=err,
                )
        await db.commit()
        if ok_count:
            await log_repo.write(
                f"[INFO] Album {album_idx} in {folder.name}: {ok_count}/{len(file_ids)} photos (individual upload)",
                level=LogLevel.INFO,
                session_id=self.session_id,
            )
        else:
            await log_repo.write(
                f"[ERROR] Album {album_idx} in {folder.name} failed: {album_err}",
                level=LogLevel.ERROR,
                session_id=self.session_id,
            )
        await db.commit()
        return ok_count > 0

    async def _send_end_marker(self, folder, folder_repo, file_repo, log_repo, db):
        if folder.end_msg_id is not None:
            return
        end_text = get_settings().folder_end_template.format(
            folder_name=folder.name.upper()
        )
        try:
            msg = await self.uploader.send_text(self.channel_id, end_text)
            await folder_repo.save_end_msg_id(self.folder_id, msg.id)
            await db.commit()
            logger.info("Sent END marker", folder=folder.name, msg_id=msg.id)
            await log_repo.write(
                f"[INFO] Completed folder: {folder.name}",
                level=LogLevel.INFO,
                session_id=self.session_id,
            )
            await db.commit()
            await self._emit_progress(
                file_repo,
                message=f"Folder complete: {folder.name}",
            )
        except Exception as e:
            logger.error("Failed to send END marker", folder=folder.name, error=str(e))
