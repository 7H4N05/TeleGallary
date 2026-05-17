"""
Telegram Uploader — sends photos to a Telegram channel.

Uses ClientManager + preferred account so FloodWait can fail over to another connected account.
Image prep runs in a thread pool so the asyncio event loop stays responsive during large batches.
"""

from __future__ import annotations

import asyncio
from typing import Callable, List, Optional, Tuple

from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InputMediaPhoto, Message

from config.settings import get_settings
from telegram.floodwait_handler import FloodWaitHandler
from telegram.image_utils import cleanup_temp_path, prepare_photo_for_upload
from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramUploader:
    def __init__(
        self,
        preferred_account_id: str,
        event_emitter: Optional[Callable] = None,
    ):
        self.preferred_account_id = preferred_account_id
        self.fw_handler = FloodWaitHandler(event_emitter=event_emitter)
        self.event_emitter = event_emitter
        self._last_progress_pct: int = -1

    async def _emit_status(self, message: str, **extra):
        if not self.event_emitter:
            return
        await self.event_emitter("progress", {"message": message, **extra})

    def _progress_callback(self, label: str):
        """Pyrogram upload progress (may run on a worker thread)."""
        loop = asyncio.get_running_loop()

        def cb(current: int, total: int):
            if not total or not self.event_emitter:
                return
            pct = int(current * 100 / total)
            if pct == self._last_progress_pct and pct != 100:
                return
            self._last_progress_pct = pct

            async def _send():
                await self._emit_status(
                    f"{label}: {pct}%",
                    upload_bytes_current=current,
                    upload_bytes_total=total,
                )

            asyncio.run_coroutine_threadsafe(_send(), loop)

        return cb

    async def _prepare_paths_async(self, photo_paths: List[str]) -> Tuple[List[str], List[Tuple[str, bool]]]:
        """Prepare files off the event loop (PIL is CPU-heavy)."""
        n = len(photo_paths)
        if n:
            await self._emit_status(f"Preparing {n} photo(s) for Telegram…")
            logger.info("Preparing photos for upload", count=n)

        async def one(path: str) -> Tuple[str, bool]:
            return await asyncio.to_thread(prepare_photo_for_upload, path)

        results = await asyncio.gather(*[one(p) for p in photo_paths])
        upload_paths = [r[0] for r in results]
        cleanup = list(results)
        return upload_paths, cleanup

    @staticmethod
    def _cleanup_all(cleanup: List[Tuple[str, bool]]) -> None:
        for path, is_temp in cleanup:
            cleanup_temp_path(path, is_temp)

    async def _run_with_timeout(self, coro, timeout: int, label: str):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{label} timed out after {timeout}s") from e

    async def send_text(self, channel_id: str, text: str) -> Message:
        return await self.fw_handler.run(
            self.preferred_account_id,
            lambda c: c.send_message(channel_id, text),
        )

    async def send_album(
        self,
        channel_id: str,
        photo_paths: List[str],
        caption: Optional[str] = None,
    ) -> List[Message]:
        if not photo_paths:
            return []

        if len(photo_paths) == 1:
            msg = await self._send_single_photo(channel_id, photo_paths[0], caption)
            return [msg]

        settings = get_settings()
        upload_paths, cleanup = await self._prepare_paths_async(photo_paths)
        try:
            media = [
                InputMediaPhoto(
                    media=path,
                    caption=caption if i == 0 else None,
                )
                for i, path in enumerate(upload_paths)
            ]

            await self._emit_status(
                f"Uploading album ({len(media)} photos)…",
                current_file=photo_paths[0].split("\\")[-1].split("/")[-1],
            )
            logger.info("Sending media group", photos=len(media))

            return await self._run_with_timeout(
                self.fw_handler.run(
                    self.preferred_account_id,
                    lambda c: c.send_media_group(channel_id, media),
                ),
                settings.upload_album_timeout,
                "Album upload",
            )
        finally:
            self._cleanup_all(cleanup)
            self._last_progress_pct = -1

    async def _send_single_photo(
        self,
        channel_id: str,
        photo_path: str,
        caption: Optional[str] = None,
    ) -> Message:
        upload_path, is_temp = await asyncio.to_thread(prepare_photo_for_upload, photo_path)
        settings = get_settings()
        name = photo_path.split("\\")[-1].split("/")[-1]
        try:
            await self._emit_status(f"Uploading {name}…", current_file=name)
            progress = self._progress_callback(f"Uploading {name}")
            return await self._run_with_timeout(
                self.fw_handler.run(
                    self.preferred_account_id,
                    lambda c: c.send_photo(
                        channel_id,
                        upload_path,
                        caption=caption,
                        progress=progress,
                    ),
                ),
                settings.upload_photo_timeout,
                f"Photo upload ({name})",
            )
        finally:
            cleanup_temp_path(upload_path, is_temp)
            self._last_progress_pct = -1

    async def upload_photo_with_retry(
        self,
        channel_id: str,
        photo_path: str,
        max_retries: int = None,
    ) -> Optional[Message]:
        settings = get_settings()
        max_retries = max_retries or settings.max_retries
        base_delay = settings.retry_base_delay
        max_delay = settings.retry_max_delay

        try:
            upload_path, is_temp = await asyncio.to_thread(prepare_photo_for_upload, photo_path)
        except ValueError as e:
            logger.error("Photo preparation failed", file=photo_path, error=str(e))
            return None

        name = photo_path.split("\\")[-1].split("/")[-1]
        try:
            for attempt in range(1, max_retries + 1):
                try:
                    progress = self._progress_callback(f"Uploading {name}")
                    return await self._run_with_timeout(
                        self.fw_handler.run(
                            self.preferred_account_id,
                            lambda c: c.send_photo(
                                channel_id,
                                upload_path,
                                progress=progress,
                            ),
                        ),
                        settings.upload_photo_timeout,
                        f"Photo upload ({name})",
                    )
                except FloodWait:
                    raise
                except (RPCError, OSError, TimeoutError, Exception) as e:
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "Upload attempt failed",
                        file=photo_path,
                        attempt=attempt,
                        max_retries=max_retries,
                        error=str(e),
                        retry_in=delay,
                    )
                    if attempt == max_retries:
                        logger.error(
                            "Permanent upload failure",
                            file=photo_path,
                            error=str(e),
                        )
                        return None
                    await asyncio.sleep(delay)
            return None
        finally:
            cleanup_temp_path(upload_path, is_temp)
            self._last_progress_pct = -1

    async def upload_photos_individually(
        self,
        channel_id: str,
        photo_paths: List[str],
    ) -> List[Optional[Message]]:
        """Upload one-by-one; returns one Message or None per path."""
        results: List[Optional[Message]] = []
        for path in photo_paths:
            msg = await self.upload_photo_with_retry(channel_id, path)
            results.append(msg)
        return results
