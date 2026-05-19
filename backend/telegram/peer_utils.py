"""
Telegram peer / channel ID normalization, resolution, and cache restoration.

ROOT CAUSE OF PEER_ID_INVALID
==============================
Pyrogram uses MemoryStorage when a session_string is provided.  MemoryStorage
holds no peer access-hashes between reconnects.  Telegram's MTProto layer
*requires* the access_hash (a 64-bit secret token) to address a supergroup /
channel by numeric ID — without it every RPC (get_chat, send_photo, …) returns
PEER_ID_INVALID regardless of whether the account is a member.

get_chat() FAILURE on private channels
======================================
For a *private* channel (no public username), get_chat(numeric_id) itself fails
with PEER_ID_INVALID because Pyrogram constructs InputChannel(id, access_hash=0)
and Telegram rejects it.  The ONLY reliable way to obtain the access_hash for a
private channel in a fresh MemoryStorage session is to iterate get_dialogs()
until we find the channel entity (which carries the access_hash).

PERSISTENCE & RESTORE
=====================
Once we have the access_hash we:
1. Persist it in UploadSession.channel_access_hash (our DB column).
2. On every subsequent reconnect call restore_peer_in_session() which inserts
   (channel_id, access_hash) into MemoryStorage — a cheap, zero-network-RTT op.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, Union
from urllib.parse import urlparse

from pyrogram import Client
from pyrogram.errors import ChannelInvalid, ChannelPrivate, PeerIdInvalid, UsernameInvalid
from pyrogram.raw.types import InputPeerChannel
from pyrogram.types import Chat

from utils.logger import get_logger

logger = get_logger(__name__)

_TME_C_RE = re.compile(r"(?:https?://)?(?:www\.)?t\.me/c/(\d+)", re.I)
_TME_USER_RE = re.compile(r"(?:https?://)?(?:www\.)?t\.me/([A-Za-z0-9_]+)", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def normalize_channel_id(raw: str) -> str:
    """
    Normalize user input to a form Pyrogram accepts.

    Accepts: @username, -100…, bare id from t.me/c/…, full t.me links.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("Channel ID is required")

    m = _TME_C_RE.search(s)
    if m:
        return _bare_channel_to_peer(int(m.group(1)))

    if s.startswith("http://") or s.startswith("https://"):
        path = urlparse(s).path.strip("/")
        if path.startswith("c/"):
            parts = path.split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                return _bare_channel_to_peer(int(parts[1]))
        if path and not path.startswith("+"):
            user = path.split("/")[0]
            if user and user not in ("c", "joinchat"):
                return f"@{user.lstrip('@')}"
        raise ValueError(
            "Could not parse channel link. Use @username, -100… id, or a t.me/c/… post URL."
        )

    m = _TME_USER_RE.match(s)
    if m and not s.lower().startswith("t.me/c"):
        name = m.group(1)
        if name not in ("c", "joinchat", "+"):
            return f"@{name.lstrip('@')}"

    if s.startswith("@"):
        return s

    # Plain numeric: t.me/c style (positive) or already a Telegram peer id (negative)
    cleaned = s.replace(" ", "")
    if cleaned.lstrip("-").isdigit():
        n = int(cleaned)
        if n > 0:
            return _bare_channel_to_peer(n)
        return str(n)

    if s.startswith("t.me/"):
        inner = s[5:].strip("/")
        if inner.startswith("c/"):
            parts = inner.split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                return _bare_channel_to_peer(int(parts[1]))
        if inner and not inner.startswith("+"):
            return f"@{inner.lstrip('@')}"

    return s


def _bare_channel_to_peer(bare_id: int) -> str:
    """Convert internal channel id (from t.me/c/) to full peer id."""
    return str(-(1_000_000_000_000 + bare_id))


def _is_canonical_peer_id(s: str) -> bool:
    """Return True if s is already a fully-formed Telegram supergroup/channel peer id."""
    return bool(re.fullmatch(r"-100\d{10,13}", s))


def _channel_int_id(peer_id_str: str) -> int:
    """Extract the bare integer channel_id from a canonical -100… string."""
    return abs(int(peer_id_str)) - 1_000_000_000_000


# ─────────────────────────────────────────────────────────────────────────────
# Dialog-based peer discovery — the reliable fallback for private channels
# ─────────────────────────────────────────────────────────────────────────────

async def _find_peer_via_dialogs(
    client: Client,
    peer_id: int,
    max_dialogs: int = 500,
) -> Optional[int]:
    """
    Iterate through the account's dialogs to find a channel/supergroup by its
    full Pyrogram peer ID (e.g. -1003991955726), then return its access_hash.

    This is the ONLY reliable way to obtain the access_hash for a PRIVATE
    channel (no public username) in a fresh MemoryStorage session.  It works
    because GetDialogs returns full peer entities including access_hashes.

    We fetch up to `max_dialogs` (default 500) to avoid hanging accounts
    with thousands of chats.  Since the upload bot recently posted to the
    channel it should appear early in the most-recent-first ordering.

    Args:
        peer_id: Full Pyrogram-style peer id (negative, e.g. -1003991955726).
                 This matches chat.id from dialog iteration.
    """
    logger.info(
        "Searching account dialogs for channel peer",
        peer_id=peer_id,
        max_dialogs=max_dialogs,
    )
    count = 0
    try:
        async for dialog in client.get_dialogs():
            count += 1
            chat = dialog.chat
            # chat.id is the full Pyrogram peer id (e.g. -1003991955726)
            if chat and chat.id == peer_id:
                logger.info("Found channel in dialogs", peer_id=peer_id, at_dialog=count)
                # The peer is now in MemoryStorage — extract the access_hash.
                try:
                    raw_peer = await client.resolve_peer(peer_id)
                    if isinstance(raw_peer, InputPeerChannel):
                        return raw_peer.access_hash
                except Exception as e:
                    logger.warning("resolve_peer after dialog find failed", error=str(e))
                return None  # found but couldn't extract hash
            if count >= max_dialogs:
                logger.warning(
                    "Channel not found in first dialogs — stopping search",
                    searched=count,
                    peer_id=peer_id,
                )
                break
    except Exception as e:
        logger.warning("get_dialogs failed", error=str(e))

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Peer cache restoration — called after every reconnect
# ─────────────────────────────────────────────────────────────────────────────

async def restore_peer_in_session(
    client: Client,
    channel_id_str: str,
    access_hash: int,
) -> None:
    """
    Inject a (channel_id, access_hash) pair into Pyrogram's MemoryStorage so
    that all subsequent RPCs using the numeric channel_id succeed without a
    network round-trip.

    This is the cheap fix for post-reconnect PEER_ID_INVALID: since we stored
    the access_hash in our DB on the first successful resolution, we can restore
    it into MemoryStorage on every reconnect in O(1) — no network call needed.
    """
    if not access_hash:
        logger.warning(
            "restore_peer_in_session called with no access_hash — skipping",
            channel_id=channel_id_str,
        )
        return

    # Pyrogram's storage.get_peer_by_id() looks up by the FULL peer id
    # (e.g. -1003991955726), not the bare channel id.  The `id` column in
    # the peers table must therefore store the full peer id.  When building
    # an InputPeerChannel, get_input_peer() converts it back to the bare id
    # via utils.get_channel_id().
    full_peer_id = int(channel_id_str)  # e.g. -1003991955726

    # Pyrogram storage.update_peers() signature:
    # List[Tuple[id, access_hash, type, username, phone_number]]
    try:
        await client.storage.update_peers([(full_peer_id, access_hash, "channel", None, None)])
        logger.info(
            "Peer cache restored from stored access_hash",
            channel_id=channel_id_str,
            full_peer_id=full_peer_id,
        )
    except Exception as e:
        logger.warning(
            "Could not restore peer cache via storage.update_peers",
            error=str(e),
            channel_id=channel_id_str,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Channel resolution — called once when creating / resuming a session
# ─────────────────────────────────────────────────────────────────────────────

async def resolve_upload_channel(
    client: Client, raw_channel_id: str
) -> Tuple[str, Optional[int]]:
    """
    Normalize, resolve, and return ``(canonical_peer_id, access_hash)``.

    Resolution strategy (in order):
    1. get_chat(normalized) — works for @username channels and channels already
       in the MemoryStorage peer cache.
    2. If that fails with PeerIdInvalid AND the id is a canonical -100… peer id,
       fall back to get_dialogs() scan — the only reliable path for PRIVATE
       channels in a fresh MemoryStorage session.
    3. If dialogs scan finds the channel, extract its access_hash and return.
    4. If dialogs scan fails (channel not found in first 500 dialogs), trust the
       stored id and return None for the hash.  Uploads will fail until the
       access_hash is obtained through some other means.

    The access_hash MUST be persisted by the caller in the UploadSession row.
    """
    normalized = normalize_channel_id(raw_channel_id)

    chat: Optional[Chat] = None

    try:
        chat = await client.get_chat(normalized)
    except PeerIdInvalid:
        if not _is_canonical_peer_id(normalized):
            raise ValueError(
                "Invalid channel. Use -1001234567890 (from a channel post link), @username, "
                "or join the channel with this account first (admin rights required to post)."
            )
        # ── Fallback: scan dialogs for private channel ────────────────────
        full_peer_id = int(normalized)  # e.g. -1003991955726 — matches chat.id
        logger.info(
            "get_chat failed for canonical peer — scanning dialogs to find access_hash",
            peer_id=normalized,
        )
        access_hash = await _find_peer_via_dialogs(client, full_peer_id)
        if access_hash is not None:
            logger.info(
                "Channel access_hash obtained via dialogs scan",
                peer_id=normalized,
                access_hash_present=True,
            )
            return normalized, access_hash
        else:
            logger.warning(
                "Channel not found in dialogs — peer resolution failed. "
                "The account may not be a member, or the channel is too old in the dialog list. "
                "Try opening the channel in the Telegram app with this account first.",
                peer_id=normalized,
            )
            return normalized, None

    except (ChannelInvalid, UsernameInvalid) as e:
        raise ValueError(
            "Invalid channel. Use -1001234567890 (from a channel post link), @username, "
            "or join the channel with this account first (admin rights required to post)."
        ) from e
    except ChannelPrivate as e:
        raise ValueError(
            "Channel is private or this account is not a member. "
            "Open the channel in Telegram with this account, then try again."
        ) from e

    # get_chat() succeeded — extract access_hash via resolve_peer()
    peer_id = str(chat.id)
    access_hash: Optional[int] = None
    try:
        raw_peer = await client.resolve_peer(chat.id)
        if isinstance(raw_peer, InputPeerChannel):
            access_hash = raw_peer.access_hash
        else:
            logger.warning(
                "resolve_peer returned unexpected type — access_hash unavailable",
                peer_type=type(raw_peer).__name__,
                peer_id=peer_id,
            )
    except Exception as e:
        logger.warning("Could not extract access_hash via resolve_peer", error=str(e))

    if access_hash is not None:
        logger.info(
            "Channel resolved",
            input=raw_channel_id,
            normalized=normalized,
            peer_id=peer_id,
            title=chat.title,
            access_hash_present=True,
        )
    else:
        logger.warning(
            "Channel resolved but access_hash is None — PEER_ID_INVALID will recur on reconnect",
            peer_id=peer_id,
        )

    return peer_id, access_hash


async def ensure_channel_peer(
    client: Client, channel_id: Union[str, int]
) -> Tuple[str, Optional[int]]:
    """Resolve if needed; return (peer_id_str, access_hash)."""
    return await resolve_upload_channel(client, str(channel_id))
