"""
Telegram peer / channel ID normalization and validation.

Users often paste the numeric segment from t.me/c/1234567890/42 instead of the
full peer id (-1001234567890). Pyrogram then raises PEER_ID_INVALID.
"""

from __future__ import annotations

import re
from typing import Union
from urllib.parse import urlparse

from pyrogram import Client
from pyrogram.errors import ChannelInvalid, ChannelPrivate, PeerIdInvalid, UsernameInvalid
from pyrogram.types import Chat

from utils.logger import get_logger

logger = get_logger(__name__)

_TME_C_RE = re.compile(r"(?:https?://)?(?:www\.)?t\.me/c/(\d+)", re.I)
_TME_USER_RE = re.compile(r"(?:https?://)?(?:www\.)?t\.me/([A-Za-z0-9_]+)", re.I)


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
    # Pyrogram canonical form: -100 followed by digits, total length 13-16 chars.
    return bool(re.fullmatch(r"-100\d{10,13}", s))


async def resolve_upload_channel(client: Client, raw_channel_id: str) -> str:
    """
    Normalize, resolve via get_chat, and return the canonical peer id string.

    Ensures the account can access the channel and warms Pyrogram's peer cache.

    If the input is already a canonical -100… peer id and Pyrogram raises
    PeerIdInvalid (peer not yet in in-memory cache after a restart), the stored
    id is trusted and returned as-is — it was validated when the session was
    first created.
    """
    normalized = normalize_channel_id(raw_channel_id)
    try:
        chat: Chat = await client.get_chat(normalized)
    except PeerIdInvalid as e:
        # Pyrogram's in-memory session loses peer cache on restart.  If the id
        # already looks like a valid canonical peer id, trust it and skip the
        # get_chat warm-up — the orchestrator will resolve it on first send.
        if _is_canonical_peer_id(normalized):
            logger.warning(
                "PeerIdInvalid on canonical peer id — peer not in cache yet; trusting stored id",
                peer_id=normalized,
            )
            return normalized
        raise ValueError(
            "Invalid channel. Use -1001234567890 (from a channel post link), @username, "
            "or join the channel with this account first (admin rights required to post)."
        ) from e
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

    peer_id = str(chat.id)
    logger.info(
        "Channel resolved",
        input=raw_channel_id,
        normalized=normalized,
        peer_id=peer_id,
        title=chat.title,
    )
    return peer_id


async def ensure_channel_peer(client: Client, channel_id: Union[str, int]) -> str:
    """Resolve if needed; return peer id string."""
    return await resolve_upload_channel(client, str(channel_id))
