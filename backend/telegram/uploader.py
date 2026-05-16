"""
Telegram Uploader — sends photos to a Telegram channel.

Uses ClientManager + preferred account so FloodWait can fail over to another connected account.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, List, Optional

from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InputMediaPhoto, Message

from config.settings import get_settings
from telegram.floodwait_handler import FloodWaitHandler
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

        media = [
            InputMediaPhoto(
                media=path,
                caption=caption if i == 0 else None,
            )
            for i, path in enumerate(photo_paths)
        ]

        return await self.fw_handler.run(
            self.preferred_account_id,
            lambda c: c.send_media_group(channel_id, media),
        )

    async def _send_single_photo(
        self,
        channel_id: str,
        photo_path: str,
        caption: Optional[str] = None,
    ) -> Message:
        return await self.fw_handler.run(
            self.preferred_account_id,
            lambda c: c.send_photo(channel_id, photo_path, caption=caption),
        )

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

        for attempt in range(1, max_retries + 1):
            try:
                return await self.fw_handler.run(
                    self.preferred_account_id,
                    lambda c: c.send_photo(channel_id, photo_path),
                )
            except FloodWait:
                raise
            except (RPCError, OSError, Exception) as e:
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
