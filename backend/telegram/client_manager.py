"""
Pyrogram Client Manager — manages a pool of Telegram client instances.

Supports:
- Multiple accounts
- Automatic failover during FloodWait
- Session string storage/reload
"""

import asyncio
from pathlib import Path
from typing import Dict, Optional

from pyrogram import Client
from pyrogram.errors import FloodWait

from config.settings import get_settings
from utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class ManagedClient:
    """Wraps a Pyrogram client with metadata."""

    def __init__(self, account_id: str, phone: str, client: Client):
        self.account_id = account_id
        self.phone = phone
        self.client = client
        self.in_floodwait = False
        self.floodwait_until: Optional[float] = None  # unix timestamp


class ClientManager:
    """
    Singleton-style pool of active Pyrogram clients.
    One instance per running backend.
    """

    def __init__(self):
        self._clients: Dict[str, ManagedClient] = {}
        self._lock = asyncio.Lock()
        Path(settings.session_dir).mkdir(parents=True, exist_ok=True)

    async def add_client(
        self,
        account_id: str,
        phone: str,
        api_id: int,
        api_hash: str,
        session_string: Optional[str] = None,
    ) -> Client:
        """Create and start a Pyrogram client for an account."""
        async with self._lock:
            if account_id in self._clients:
                return self._clients[account_id].client

            client = Client(
                name=account_id,
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,
                workdir=settings.session_dir,
                in_memory=bool(session_string),  # use in-memory when we have session str
            )
            managed = ManagedClient(account_id=account_id, phone=phone, client=client)
            self._clients[account_id] = managed
            logger.info("Client registered", account_id=account_id, phone=phone)
            return client

    async def start_client(self, account_id: str) -> bool:
        """Connect a client to Telegram."""
        managed = self._clients.get(account_id)
        if not managed:
            return False
        try:
            if not managed.client.is_connected:
                await managed.client.start()
            logger.info("Client connected", account_id=account_id)
            return True
        except Exception as e:
            logger.error("Client connect failed", account_id=account_id, error=str(e))
            return False

    async def stop_client(self, account_id: str) -> None:
        managed = self._clients.get(account_id)
        if managed and managed.client.is_connected:
            await managed.client.stop()
            logger.info("Client disconnected", account_id=account_id)

    async def get_client(self, account_id: str) -> Optional[Client]:
        managed = self._clients.get(account_id)
        return managed.client if managed else None

    def pick_managed_client(self, preferred_account_id: str) -> Optional[ManagedClient]:
        """
        Return a connected client ready to send (not in active FloodWait cooldown).
        Prefers `preferred_account_id`; otherwise any other available account (failover).
        """
        import time

        now = time.time()

        preferred = self._clients.get(preferred_account_id)
        if preferred and (
            not preferred.in_floodwait
            or (preferred.floodwait_until and preferred.floodwait_until <= now)
        ):
            preferred.in_floodwait = False
            preferred.floodwait_until = None
            return preferred

        for acc_id, managed in self._clients.items():
            if acc_id == preferred_account_id:
                continue
            if not managed.in_floodwait or (
                managed.floodwait_until and managed.floodwait_until <= now
            ):
                managed.in_floodwait = False
                managed.floodwait_until = None
                logger.info("Failover candidate account", account_id=acc_id)
                return managed

        return None

    async def get_available_client(self, preferred_account_id: str) -> Optional[ManagedClient]:
        """Async alias for code that awaited the old API."""
        return self.pick_managed_client(preferred_account_id)

    def mark_floodwait(self, account_id: str, wait_seconds: int) -> None:
        import time
        managed = self._clients.get(account_id)
        if managed:
            managed.in_floodwait = True
            managed.floodwait_until = time.time() + wait_seconds
            logger.warning(
                "Account in FloodWait",
                account_id=account_id,
                seconds=wait_seconds,
            )

    def clear_floodwait(self, account_id: str) -> None:
        managed = self._clients.get(account_id)
        if managed:
            managed.in_floodwait = False
            managed.floodwait_until = None

    def get_session_string(self, account_id: str) -> Optional[str]:
        managed = self._clients.get(account_id)
        if managed and managed.client.session_string:
            return managed.client.session_string
        return None

    async def remove_client(self, account_id: str) -> None:
        async with self._lock:
            managed = self._clients.pop(account_id, None)
            if managed and managed.client.is_connected:
                await managed.client.stop()


# Global singleton
_client_manager: Optional[ClientManager] = None


def get_client_manager() -> ClientManager:
    global _client_manager
    if _client_manager is None:
        _client_manager = ClientManager()
    return _client_manager
