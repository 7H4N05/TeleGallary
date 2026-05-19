"""
Folder Processor — handles upload of a single folder end-to-end.

v2.0 — Integrated with metrics, adaptive controller, and reliability system.

Flow per folder:
1. Check if already completed (skip)
2. Send START marker if not already sent
3. Load pending files from DB
4. Batch into albums of 10
5. Upload each album with per-phase timing
6. Track per-file success/failure
7. Record metrics for ETA and adaptive tuning
8. Send END marker
9. Mark folder complete
"""

import asyncio
import time
from typing import Callable, Optional

from db.database import AsyncSessionLocal
from db.models import FileStatus, FolderStatus
from db.repositories.failed_repo import FailedUploadRepository
from db.repositories.file_repo import FileRepository
from db.repositories.folder_repo import FolderRepository
from db.repositories.log_repo import LogRepository
from db.models import LogLevel
from config.settings import get_settings
from monitoring.metrics import UploadMetrics, UploadTiming
from monitoring.reliability_controller import ReliabilityController
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
        metrics: Optional[UploadMetrics] = None,
        reliability: Optional[ReliabilityController] = None,
    ):
        self.folder_id = folder_id
        self.session_id = session_id
        self.channel_id = channel_id
        self.uploader = uploader
        self.event_emitter = event_emitter
        self.stop_event = stop_event
        self.pause_event = pause_event
        self._metrics = metrics
        self._reliability = reliability

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

        v2.1: Per-album scoped DB sessions instead of one long-lived session.
        The previous approach held a single SQLAlchemy session for the entire
        folder (potentially hours), accumulating dirty objects in the identity
        map and pinning a SQLite WAL writer — contributing to memory growth.
        """
        # ── Phase 1: Folder-level setup (short-lived session) ──────────
        async with AsyncSessionLocal() as db:
            folder_repo = FolderRepository(db)
            file_repo = FileRepository(db)
            log_repo = LogRepository(db)

            folder = await folder_repo.get_by_id(self.folder_id)
            if not folder:
                return False

            if folder.status == FolderStatus.completed:
                logger.info("Folder already completed, skipping", folder=folder.name)
                return True

            folder_name = folder.name  # cache for use outside session

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

            # Snapshot file data we need outside the session
            album_snapshots = []
            for album_idx, album_files in sorted_albums:
                album_snapshots.append((
                    album_idx,
                    [(f.id, f.path, f.filename, f.size_bytes) for f in album_files],
                ))

        # ── Phase 2: Upload albums (per-album scoped sessions) ─────────
        for album_idx, file_tuples in album_snapshots:
            await self._wait_if_paused()
            if self.stop_event.is_set():
                return False

            file_ids = [ft[0] for ft in file_tuples]
            file_paths = [ft[1] for ft in file_tuples]
            file_names = [ft[2] for ft in file_tuples]
            file_sizes = [ft[3] for ft in file_tuples]

            # Mark uploading + emit progress in a scoped session
            async with AsyncSessionLocal() as db:
                file_repo = FileRepository(db)
                await self._emit_progress(
                    file_repo,
                    current_folder=folder_name,
                    current_album=album_idx,
                    current_file=file_names[0],
                )
                for fid in file_ids:
                    await file_repo.mark_uploading(fid)
                await db.commit()

            logger.info(
                "Uploading album",
                folder=folder_name,
                album_idx=album_idx,
                photos=len(file_tuples),
            )

            # v2: Record per-upload timing
            upload_start = time.monotonic()

            # Upload (uses Telegram client, no DB needed)
            uploaded = await self._upload_album_scoped(
                album_idx=album_idx,
                file_ids=file_ids,
                file_paths=file_paths,
                file_sizes=file_sizes,
                folder_name=folder_name,
            )

            upload_end = time.monotonic()

            # v2: Record metrics for this album batch
            if self._metrics and self._reliability:
                for i, fid in enumerate(file_ids):
                    timing = UploadTiming(
                        file_id=fid,
                        file_size_bytes=file_sizes[i],
                        started_at=upload_start,
                        completed_at=upload_end,
                        # Distribute total time across phases (approximation)
                        network_duration=(upload_end - upload_start) / len(file_ids),
                        success=uploaded,
                    )
                    self._metrics.record_upload(timing)

                if uploaded:
                    self._reliability.on_upload_success()
                else:
                    self._reliability.on_upload_failure()

            # v2: Apply pacing delay from adaptive controller
            if self._reliability:
                pacing = self._reliability.adaptive.params.pacing_delay_seconds
                if pacing > 0:
                    await asyncio.sleep(pacing)

            if not uploaded:
                async with AsyncSessionLocal() as db:
                    file_repo = FileRepository(db)
                    await self._emit_progress(
                        file_repo,
                        current_folder=folder_name,
                        current_album=album_idx,
                        message=f"Album {album_idx} failed ({len(file_ids)} files)",
                    )

        # ── Phase 3: Send END marker (short-lived session) ─────────────
        async with AsyncSessionLocal() as db:
            folder_repo = FolderRepository(db)
            file_repo = FileRepository(db)
            log_repo = LogRepository(db)
            folder = await folder_repo.get_by_id(self.folder_id)
            if folder:
                await self._send_end_marker(folder, folder_repo, file_repo, log_repo, db)
        return True

    async def _upload_album_scoped(
        self,
        album_idx: int,
        file_ids: list,
        file_paths: list,
        file_sizes: list,
        folder_name: str,
    ) -> bool:
        """Upload an album with its own scoped DB session for result persistence."""
        try:
            messages = await self.uploader.send_album(self.channel_id, file_paths)

            if messages and len(messages) == len(file_ids):
                async with AsyncSessionLocal() as db:
                    file_repo = FileRepository(db)
                    folder_repo = FolderRepository(db)
                    log_repo = LogRepository(db)
                    for fid, msg in zip(file_ids, messages):
                        await file_repo.mark_uploaded(fid, msg.id)
                        await folder_repo.increment_uploaded(self.folder_id)
                    await log_repo.write(
                        f"[INFO] Uploaded album {album_idx} in {folder_name} ({len(file_ids)} photos)",
                        level=LogLevel.INFO,
                        session_id=self.session_id,
                    )
                    await db.commit()
                    await self._emit_progress(
                        file_repo,
                        current_folder=folder_name,
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
                folder=folder_name,
                error=str(album_err),
            )
            return await self._upload_individually_scoped(
                album_idx=album_idx,
                file_ids=file_ids,
                file_paths=file_paths,
                file_sizes=file_sizes,
                folder_name=folder_name,
                album_err=album_err,
            )

    async def _upload_individually_scoped(
        self,
        album_idx: int,
        file_ids: list,
        file_paths: list,
        file_sizes: list,
        folder_name: str,
        album_err: Exception,
    ) -> bool:
        """Individual upload fallback with scoped DB sessions."""
        ok_count = 0
        for fid, path, size in zip(file_ids, file_paths, file_sizes):
            file_start = time.monotonic()
            try:
                msg = await self.uploader.upload_photo_with_retry(self.channel_id, path)
                if msg:
                    async with AsyncSessionLocal() as db:
                        file_repo = FileRepository(db)
                        folder_repo = FolderRepository(db)
                        await file_repo.mark_uploaded(fid, msg.id)
                        await folder_repo.increment_uploaded(self.folder_id)
                        await db.commit()
                    ok_count += 1

                    if self._metrics:
                        timing = UploadTiming(
                            file_id=fid,
                            file_size_bytes=size,
                            started_at=file_start,
                            completed_at=time.monotonic(),
                            network_duration=time.monotonic() - file_start,
                            success=True,
                        )
                        self._metrics.record_upload(timing)
                else:
                    raise RuntimeError("Upload returned no message")
            except Exception as e:
                err = str(e)
                async with AsyncSessionLocal() as db:
                    file_repo = FileRepository(db)
                    failed_repo = FailedUploadRepository(db)
                    await file_repo.mark_failed(fid, err)
                    await failed_repo.create(
                        file_id=fid,
                        session_id=self.session_id,
                        error_type=type(e).__name__,
                        error_message=err,
                    )
                    await db.commit()

                if self._metrics:
                    timing = UploadTiming(
                        file_id=fid,
                        file_size_bytes=size,
                        started_at=file_start,
                        completed_at=time.monotonic(),
                        success=False,
                        error_type=type(e).__name__,
                    )
                    self._metrics.record_upload(timing)

        async with AsyncSessionLocal() as db:
            log_repo = LogRepository(db)
            if ok_count:
                await log_repo.write(
                    f"[INFO] Album {album_idx} in {folder_name}: {ok_count}/{len(file_ids)} photos (individual upload)",
                    level=LogLevel.INFO,
                    session_id=self.session_id,
                )
            else:
                await log_repo.write(
                    f"[ERROR] Album {album_idx} in {folder_name} failed: {album_err}",
                    level=LogLevel.ERROR,
                    session_id=self.session_id,
                )
            await db.commit()
        return ok_count > 0

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
            # v2: Per-file timing for individual uploads
            file_start = time.monotonic()
            try:
                msg = await self.uploader.upload_photo_with_retry(self.channel_id, path)
                if msg:
                    await file_repo.mark_uploaded(fid, msg.id)
                    await folder_repo.increment_uploaded(self.folder_id)
                    ok_count += 1

                    # v2: Record successful individual upload timing
                    if self._metrics:
                        timing = UploadTiming(
                            file_id=f.id,
                            file_size_bytes=f.size_bytes,
                            started_at=file_start,
                            completed_at=time.monotonic(),
                            network_duration=time.monotonic() - file_start,
                            success=True,
                        )
                        self._metrics.record_upload(timing)
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
                # v2: Record failed upload timing
                if self._metrics:
                    timing = UploadTiming(
                        file_id=f.id,
                        file_size_bytes=f.size_bytes,
                        started_at=file_start,
                        completed_at=time.monotonic(),
                        success=False,
                        error_type=type(e).__name__,
                    )
                    self._metrics.record_upload(timing)

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
