"""
FloodWait Handler — wraps Pyrogram calls with FloodWait sleep and optional multi-account failover.

v2.0 — Integrated with metrics and reliability controller.

Reconnect behaviour
===================
Pyrogram's MemoryStorage loses the peer access_hash on every reconnect.  After
re-starting the client this handler calls restore_peer_in_session() with the
access_hash that was stored in the DB when the session was first created.  This
injection is cheap (no network round-trip) and ensures all subsequent RPCs that
address the channel by numeric ID succeed immediately.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Awaitable, Callable, Optional

from pyrogram import Client
from pyrogram.errors import FloodWait, NetworkMigrate, SlowmodeWait

from config.settings import get_settings
from telegram.client_manager import get_client_manager
from telegram.peer_utils import restore_peer_in_session
from utils.logger import get_logger

logger = get_logger(__name__)


class FloodWaitHandler:
    """
    On FloodWait:
    1. Mark the account that was rate-limited
    2. If another connected account is available and failover is enabled, retry immediately
    3. Otherwise sleep with countdown ticks, then clear cooldown and retry

    v2: Reports FloodWait events and reconnects to the reliability controller.
    """

    def __init__(
        self,
        event_emitter: Optional[Callable] = None,
        channel_access_hash: Optional[int] = None,
    ):
        self.event_emitter = event_emitter
        # The access_hash stored in the DB for the upload channel.
        # Injected into MemoryStorage after every reconnect so RPCs succeed.
        self._channel_access_hash = channel_access_hash

    async def run(
        self,
        preferred_account_id: str,
        invoker: Callable[[Client], Awaitable[Any]],
        max_floodwait: int = 3600,
        channel_id: str | None = None,
    ):
        mgr = get_client_manager()
        while True:
            managed = mgr.pick_managed_client(preferred_account_id)
            if managed is None:
                logger.warning("No Telegram client available (all in cooldown?)", preferred=preferred_account_id)
                await asyncio.sleep(1)
                continue

            cur_id = managed.account_id

            # ── Ensure the client is actually connected ───────────────────────
            if not managed.client.is_connected:
                logger.info("Client disconnected — reconnecting", account_id=cur_id)
                try:
                    await managed.client.start()
                    logger.info("Client reconnected", account_id=cur_id)

                    # v2: Report reconnect to reliability controller
                    self._report_reconnect()

                except Exception as e:
                    logger.warning("Reconnect failed", account_id=cur_id, error=str(e))
                    await asyncio.sleep(2)
                    continue

                # Restore peer cache immediately after reconnect.
                # MemoryStorage is empty again after start(); inject the stored
                # access_hash so all RPCs addressing the channel by numeric ID
                # succeed without a network round-trip.
                if channel_id and self._channel_access_hash:
                    await restore_peer_in_session(
                        managed.client, channel_id, self._channel_access_hash
                    )

            try:
                return await invoker(managed.client)
            except FloodWait as e:
                wait = float(e.value) + float(get_settings().floodwait_safety_buffer)
                wait_int = max(1, int(math.ceil(wait)))
                logger.warning(
                    "FloodWait received",
                    account_id=cur_id,
                    wait_seconds=wait_int,
                )

                if wait_int > max_floodwait:
                    raise RuntimeError(
                        f"FloodWait {wait_int}s exceeds maximum {max_floodwait}s"
                    ) from e

                mgr.mark_floodwait(cur_id, wait_int)

                # v2: Report FloodWait to reliability controller
                self._report_floodwait(wait_int)

                if self.event_emitter:
                    await self.event_emitter(
                        "floodwait",
                        {
                            "account_id": cur_id,
                            "floodwait_account": cur_id,
                            "wait_seconds": wait_int,
                            "floodwait_seconds": wait_int,
                        },
                    )

                use_failover = get_settings().failover_on_floodwait
                other = mgr.pick_managed_client(preferred_account_id) if use_failover else None
                if other and other.account_id != cur_id:
                    logger.info(
                        "FloodWait failover — retrying with alternate account",
                        from_account=cur_id,
                        to_account=other.account_id,
                    )
                    if self.event_emitter:
                        await self.event_emitter(
                            "failover",
                            {
                                "from_account_id": cur_id,
                                "to_account_id": other.account_id,
                                "floodwait_seconds": wait_int,
                                "message": f"Switched upload to account {other.account_id[:8]}… (FloodWait on primary)",
                            },
                        )
                    continue

                for remaining in range(wait_int, 0, -1):
                    if self.event_emitter:
                        await self.event_emitter(
                            "floodwait_tick",
                            {
                                "account_id": cur_id,
                                "floodwait_account": cur_id,
                                "remaining_seconds": remaining,
                            },
                        )
                    await asyncio.sleep(1)

                logger.info("FloodWait elapsed, resuming", account_id=cur_id)
                mgr.clear_floodwait(cur_id)

            except (NetworkMigrate, SlowmodeWait) as e:
                logger.warning("Telegram network issue", error=str(e))
                await asyncio.sleep(5)

    @staticmethod
    def _report_floodwait(wait_seconds: float):
        """Report FloodWait to the reliability controller (if active)."""
        try:
            from monitoring.reliability_controller import get_reliability_controller
            ctrl = get_reliability_controller()
            if ctrl:
                ctrl.on_floodwait(wait_seconds)
        except Exception:
            pass

    @staticmethod
    def _report_reconnect():
        """Report client reconnect to the reliability controller (if active)."""
        try:
            from monitoring.reliability_controller import get_reliability_controller
            ctrl = get_reliability_controller()
            if ctrl:
                ctrl.on_reconnect()
        except Exception:
            pass
