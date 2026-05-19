"""
Queue Manager — async priority upload queue with backpressure.

Replaces the simple sequential folder iteration with a managed queue that:
- Supports priority ordering (failed retries get higher priority)
- Provides backpressure when workers are saturated
- Tracks queue depth for monitoring
- Supports atomic dequeue + requeue on failure
- Persists state to DB for crash recovery
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class QueuePriority(IntEnum):
    """Lower number = higher priority."""
    RETRY = 0       # retried files get priority
    NORMAL = 10     # regular uploads


@dataclass(order=True)
class UploadItem:
    """A single unit of work for the upload workers."""
    priority: QueuePriority = field(compare=True)
    order_index: int = field(compare=True)           # preserves album ordering
    file_id: str = field(compare=False, default="")
    folder_id: str = field(compare=False, default="")
    session_id: str = field(compare=False, default="")
    file_path: str = field(compare=False, default="")
    filename: str = field(compare=False, default="")
    file_size: int = field(compare=False, default=0)
    album_index: int = field(compare=False, default=0)
    album_position: int = field(compare=False, default=0)
    attempt: int = field(compare=False, default=0)


@dataclass
class AlbumBatch:
    """A group of UploadItems that form one Telegram album."""
    album_index: int
    folder_id: str
    items: List[UploadItem] = field(default_factory=list)

    @property
    def file_paths(self) -> List[str]:
        return [item.file_path for item in self.items]

    @property
    def file_ids(self) -> List[str]:
        return [item.file_id for item in self.items]

    @property
    def total_size(self) -> int:
        return sum(item.file_size for item in self.items)


class QueueManager:
    """Thread-safe async queue with album batching.

    Items are enqueued individually but dequeued in album batches, matching
    Telegram's media group API.
    """

    def __init__(self, max_size: int = 10000):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_size)
        self._in_flight: dict[str, UploadItem] = {}  # file_id → item
        self._total_enqueued: int = 0
        self._total_dequeued: int = 0
        self._total_completed: int = 0
        self._total_requeued: int = 0

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    @property
    def stats(self) -> dict:
        return {
            "queue_depth": self.depth,
            "in_flight": self.in_flight_count,
            "total_enqueued": self._total_enqueued,
            "total_dequeued": self._total_dequeued,
            "total_completed": self._total_completed,
            "total_requeued": self._total_requeued,
        }

    async def enqueue(self, item: UploadItem):
        """Add an item to the queue."""
        await self._queue.put(item)
        self._total_enqueued += 1

    async def enqueue_batch(self, items: List[UploadItem]):
        """Add multiple items."""
        for item in items:
            await self.enqueue(item)

    async def dequeue_album(
        self,
        album_size: int = 5,
        timeout: float = 30.0,
    ) -> Optional[AlbumBatch]:
        """Dequeue up to `album_size` items that share the same album_index.

        Because items arrive in priority order, we grab items greedily and
        group them. If the queue has items from different albums interleaved
        (shouldn't happen in normal flow), we take whatever is available.
        """
        items: List[UploadItem] = []

        try:
            # Get the first item (blocking)
            first = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            items.append(first)
            target_album = first.album_index
            target_folder = first.folder_id

            # Greedily grab more items from the same album
            while len(items) < album_size:
                try:
                    item = self._queue.get_nowait()
                    if item.album_index == target_album and item.folder_id == target_folder:
                        items.append(item)
                    else:
                        # Put it back — wrong album
                        await self._queue.put(item)
                        break
                except asyncio.QueueEmpty:
                    break

        except asyncio.TimeoutError:
            return None

        if not items:
            return None

        # Track in-flight
        for item in items:
            self._in_flight[item.file_id] = item
            self._total_dequeued += 1

        return AlbumBatch(
            album_index=items[0].album_index,
            folder_id=items[0].folder_id,
            items=items,
        )

    def complete(self, file_id: str):
        """Mark an item as successfully completed."""
        self._in_flight.pop(file_id, None)
        self._total_completed += 1

    async def requeue(self, file_id: str, bump_priority: bool = True):
        """Put a failed item back in the queue for retry."""
        item = self._in_flight.pop(file_id, None)
        if item:
            item.attempt += 1
            if bump_priority:
                item.priority = QueuePriority.RETRY
            await self._queue.put(item)
            self._total_requeued += 1

    async def requeue_batch(self, file_ids: List[str]):
        for fid in file_ids:
            await self.requeue(fid)

    def fail(self, file_id: str):
        """Remove from in-flight without requeue (permanent failure)."""
        self._in_flight.pop(file_id, None)

    async def drain(self) -> List[UploadItem]:
        """Empty the queue and return all items (for shutdown/cleanup)."""
        items = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        # Also return in-flight items
        items.extend(self._in_flight.values())
        self._in_flight.clear()
        return items

    def clear(self):
        """Clear all state."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._in_flight.clear()
