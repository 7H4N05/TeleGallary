"""
Encrypt / decrypt Telegram session strings at rest when TELEGALLERY_SECRET_KEY is set.
Without the env var, values are stored as plaintext (development convenience).
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

PREFIX = "tgenc:v1:"


def _fernet_key() -> Optional[bytes]:
    secret = os.environ.get("TELEGALLERY_SECRET_KEY", "").strip()
    if not secret:
        return None
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def maybe_encrypt_session(plain: Optional[str]) -> Optional[str]:
    if not plain or plain.startswith(PREFIX):
        return plain
    key = _fernet_key()
    if key is None:
        return plain
    from cryptography.fernet import Fernet

    token = Fernet(key).encrypt(plain.encode("utf-8")).decode("ascii")
    return PREFIX + token


def maybe_decrypt_session(stored: Optional[str]) -> Optional[str]:
    if not stored:
        return stored
    if not stored.startswith(PREFIX):
        return stored
    key = _fernet_key()
    if key is None:
        raise RuntimeError(
            "Database contains encrypted session strings but TELEGALLERY_SECRET_KEY is not set"
        )
    from cryptography.fernet import Fernet

    raw = stored[len(PREFIX) :]
    return Fernet(key).decrypt(raw.encode("ascii")).decode("utf-8")
