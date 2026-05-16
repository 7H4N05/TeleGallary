import pytest


def test_encrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("TELEGALLERY_SECRET_KEY", "unit-test-secret-key-do-not-use")
    from utils.session_crypto import maybe_decrypt_session, maybe_encrypt_session

    plain = "1Aq2Ws#telegram_session_like_string"
    enc = maybe_encrypt_session(plain)
    assert enc != plain
    assert enc.startswith("tgenc:v1:")
    assert maybe_decrypt_session(enc) == plain


def test_no_encryption_without_secret(monkeypatch):
    monkeypatch.delenv("TELEGALLERY_SECRET_KEY", raising=False)
    from utils.session_crypto import maybe_encrypt_session

    assert maybe_encrypt_session("plain-session") == "plain-session"
