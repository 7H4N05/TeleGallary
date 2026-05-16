"""
Album Batcher — chunks a flat list of files into Telegram albums of ≤10.
"""

from typing import List


TELEGRAM_ALBUM_MAX = 10


def chunk_into_albums(file_paths: List[str], album_size: int = TELEGRAM_ALBUM_MAX) -> List[List[str]]:
    """
    Split an ordered list of file paths into sub-lists of at most `album_size`.
    Preserves order. Each chunk maps to one Telegram media group send call.
    """
    if not file_paths:
        return []

    return [
        file_paths[i: i + album_size]
        for i in range(0, len(file_paths), album_size)
    ]


def assign_album_indices(file_paths: List[str], album_size: int = TELEGRAM_ALBUM_MAX):
    """
    Return list of (path, album_index, album_position) tuples.
    Used when persisting file records to DB.
    """
    result = []
    for order_index, path in enumerate(file_paths):
        album_index = order_index // album_size
        album_position = order_index % album_size
        result.append((path, order_index, album_index, album_position))
    return result
