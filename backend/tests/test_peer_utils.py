"""Tests for channel ID normalization."""

import pytest

from telegram.peer_utils import normalize_channel_id


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-1001234567890", "-1001234567890"),
        ("1234567890", "-1001234567890"),
        ("@mychannel", "@mychannel"),
        ("https://t.me/c/1234567890/42", "-1001234567890"),
        ("t.me/c/999/1", "-1000000000999"),
    ],
)
def test_normalize_channel_id(raw, expected):
    assert normalize_channel_id(raw) == expected
