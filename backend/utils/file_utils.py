"""
File utility functions for scanning and categorizing photo files.
"""

import os
from pathlib import Path
from typing import List, Tuple

from config.settings import get_settings

settings = get_settings()


def is_photo(path: Path) -> bool:
    """Check if a file is a supported photo type."""
    return path.suffix.lower() in settings.photo_extensions


def scan_folders(root: str) -> List[Tuple[str, List[str]]]:
    """
    Recursively scan root directory and return list of (folder_path, [photo_paths]).
    
    Folders are sorted alphabetically. Photos within each folder are sorted
    by filename (chronological for camera-named files like IMG_XXXX).
    
    Returns only folders that contain at least one photo.
    """
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Source directory not found: {root}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {root}")

    result: List[Tuple[str, List[str]]] = []

    # os.walk already recurses; sort dirs for deterministic order
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames.sort()  # In-place sort ensures alphabetical traversal
        photos = sorted(
            [
                str(Path(dirpath) / f)
                for f in filenames
                if is_photo(Path(f))
            ]
        )
        if photos:
            result.append((dirpath, photos))

    return result


def get_folder_display_name(folder_path: str, root_path: str) -> str:
    """
    Get a clean display name for a folder relative to the root.
    
    Example: /photos/Wedding/Haldi → "Haldi"
             /photos/Wedding       → "Wedding"
    """
    rel = Path(folder_path).relative_to(Path(root_path))
    parts = rel.parts
    if not parts:
        return Path(folder_path).name
    return " / ".join(parts)


def compute_total_size(photo_paths: List[str]) -> int:
    """Return total size in bytes of all listed files."""
    total = 0
    for p in photo_paths:
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    return total


def human_size(num_bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"
