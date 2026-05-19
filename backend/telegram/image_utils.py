"""
Prepare local image files for Telegram upload.

Telegram photo rules (sendPhoto / albums):
- width + height must not exceed 10,000
- aspect ratio (long/short side) must not exceed 20:1
- file size <= 10 MB

Camera JPEGs (e.g. Sony DSC*.JPG at 6000×4000+) often violate the sum rule.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

from config.settings import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

_CONVERT_EXTENSIONS = {".heic", ".heif"}
# Stay under Telegram's 10_000 width+height cap (use margin for off-by-one)
_MAX_SUM_DIMENSIONS = 9_990
_MAX_ASPECT_RATIO = 20.0
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def prepare_photo_for_upload(path: str) -> Tuple[str, bool]:
    """
    Return (path_to_upload, is_temporary).

    If is_temporary is True, delete the file after upload.
    Raises ValueError if the file cannot be prepared.
    """
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"File not found: {path}")

    ext = src.suffix.lower()
    if ext in _CONVERT_EXTENSIONS:
        return _prepare_image_path(src, force_convert=True), True

    try:
        with Image.open(src) as im:
            im.load()
            if _needs_telegram_normalize(im, src.stat().st_size):
                return _prepare_image_path(src, force_convert=False), True
    except UnidentifiedImageError as e:
        raise ValueError(f"Unsupported or corrupt image: {src.name}") from e
    except OSError as e:
        raise ValueError(f"Cannot read image: {src.name}") from e

    return str(src.resolve()), False


def _needs_telegram_normalize(im: Image.Image, file_size: int) -> bool:
    im = ImageOps.exif_transpose(im) or im
    w, h = im.size
    if w + h > _MAX_SUM_DIMENSIONS:
        return True
    if _aspect_ratio(w, h) > _MAX_ASPECT_RATIO:
        return True
    if file_size > _MAX_BYTES:
        return True
    return False


def _aspect_ratio(w: int, h: int) -> float:
    short, long_ = sorted((max(w, 1), max(h, 1)))
    return long_ / short


def _prepare_image_path(src: Path, force_convert: bool) -> str:
    if force_convert:
        try:
            import pillow_heif  # noqa: F401

            pillow_heif.register_heif_opener()
        except ImportError:
            logger.warning("pillow-heif not installed; HEIC conversion may fail", file=str(src))

    with Image.open(src) as im:
        im.load()
        normalized = _normalize_for_telegram(im)
        result = _save_jpeg(normalized, src.stem)
        # Eagerly release the ~72 MB pixel buffer instead of waiting for GC
        normalized.close()
        return result


def _normalize_for_telegram(im: Image.Image) -> Image.Image:
    """EXIF-correct, RGB, within Telegram dimension limits."""
    im = ImageOps.exif_transpose(im) or im
    im = im.convert("RGB")
    w, h = im.size

    # Cap extreme aspect ratios by fitting inside a 20:1 box
    ratio = _aspect_ratio(w, h)
    if ratio > _MAX_ASPECT_RATIO:
        if w >= h:
            w = int(h * _MAX_ASPECT_RATIO)
        else:
            h = int(w * _MAX_ASPECT_RATIO)
        im = im.resize((max(1, w), max(1, h)), Image.Resampling.LANCZOS)
        w, h = im.size

    # width + height <= 10000 (Telegram); scale proportionally
    total = w + h
    if total > _MAX_SUM_DIMENSIONS:
        scale = _MAX_SUM_DIMENSIONS / total
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        resample = Image.Resampling.BILINEAR if scale < 0.85 else Image.Resampling.LANCZOS
        im = im.resize((new_w, new_h), resample)

    return im


def _save_jpeg(im: Image.Image, stem: str) -> str:
    fd, tmp_path = tempfile.mkstemp(prefix=f"tg_{stem}_", suffix=".jpg")
    os.close(fd)

    # Use the adaptive controller's current quality if active,
    # otherwise fall back to the static config value.
    quality = get_settings().jpeg_quality
    try:
        from monitoring.reliability_controller import get_reliability_controller
        ctrl = get_reliability_controller()
        if ctrl:
            quality = ctrl.adaptive.params.jpeg_quality
    except Exception:
        pass

    try:
        im.save(tmp_path, format="JPEG", quality=quality, optimize=False)
        return tmp_path
    except Exception:
        os.unlink(tmp_path)
        raise


def cleanup_temp_path(path: str, is_temp: bool) -> None:
    if is_temp and path:
        try:
            os.unlink(path)
        except OSError:
            pass
