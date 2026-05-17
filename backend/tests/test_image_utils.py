"""Tests for Telegram image normalization."""

from PIL import Image

from telegram.image_utils import _normalize_for_telegram, _needs_telegram_normalize


def test_6000x4000_needs_normalize():
    im = Image.new("RGB", (6000, 4000))
    assert _needs_telegram_normalize(im, 5_000_000) is True


def test_small_image_ok():
    im = Image.new("RGB", (1280, 720))
    assert _needs_telegram_normalize(im, 500_000) is False


def test_normalize_reduces_sum():
    im = Image.new("RGB", (6000, 4000))
    out = _normalize_for_telegram(im)
    assert out.width + out.height <= 9990


def test_normalize_high_mp():
    im = Image.new("RGB", (9504, 6336))
    out = _normalize_for_telegram(im)
    assert out.width + out.height <= 9990
    assert max(out.width, out.height) / min(out.width, out.height) <= 20
