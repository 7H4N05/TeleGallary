"""
Worker Pool — managed pool of upload workers with health tracking.

Each worker:
- Dequeues album batches from the QueueManager
- Uploads via TelegramUploader
- Records per-phase timing to UploadMetrics
- Reports success/failure for adaptive controller tuning
- Has a heartbeat for stall detection

The pool supports:
- Dynamic resizing (add/remove workers without restart)
- Worker recycling (periodically restart workers to prevent leaks)
- Graceful shutdown (drain current work before stopping)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from engine.queue_manager import AlbumBatch, QueueManager
from monitoring.metrics import UploadMetrics, UploadTiming
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class WorkerState:
    """Per-worker health tracking."""
    worker_id: int
    started_at: float = 0.0
    last_heartbeat: float = 0.0
    uploads_completed: int = 0
    uploads_failed: int = 0
    current_file: str = ""
    is_alive: bool = True
    task: Optional[asyncio.Task] = None


class WorkerPool:
    """Manages a dynamic pool of upload workers.

    Args:
        queue: The QueueManager to dequeue work from.
        metrics: UploadMetrics for recording timings.
        upload_fn: async callable(AlbumBatch) -> dict with results.
        on_success: called per file on success.
        on_failure: called per file on failure.
        initial_workers: starting number of workers.
        max_workers: hard ceiling.
        recycle_after: restart a worker after this many uploads (0=never).
    """

    def __init__(
        self,
        queue: QueueManager,
        metrics: UploadMetrics,
        upload_fn: Callable,
        on_batch_complete: Optional[Callable] = None,
        initial_workers: int = 1,
        max_workers: int = 4,
        recycle_after: int = 500,
        pacing_fn: Optional[Callable[[], float]] = None,
    ):
        self._queue = queue
        self._metrics = metrics
        self._upload_fn = upload_fn
        self._on_batch_complete = on_batch_complete
        self._max_workers = max_workers
        self._recycle_after = recycle_after
        self._pacing_fn = pacing_fn  # returns current pacing delay

        self._workers: Dict[int, WorkerState] = {}
        self._next_id = 0
        self._initial_workers = initial_workers
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()  # set = paused
        self._running = False

    @property
    def active_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.is_alive)

    @property
    def worker_states(self) -> List[dict]:
        return [
            {
                "worker_id": w.worker_id,
                "is_alive": w.is_alive,
                "uploads_completed": w.uploads_completed,
                "uploads_failed": w.uploads_failed,
                "current_file": w.current_file,
                "last_heartbeat": w.last_heartbeat,
                "uptime_seconds": round(time.monotonic() - w.started_at, 1) if w.started_at else 0,
            }
            for w in self._workers.values()
        ]

    async def start(self):
        """Start the worker pool."""
        self._stop_event.clear()
        self._pause_event.clear()
        self._running = True
        for _ in range(self._initial_workers):
            self._spawn_worker()
        logger.info("Worker pool started", workers=self._initial_workers)

    async def stop(self, timeout: float = 30.0):
        """Gracefully stop all workers."""
        self._stop_event.set()
        self._pause_event.clear()  # unpause so workers can exit
        self._running = False

        tasks = [w.task for w in self._workers.values() if w.task]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                t.cancel()

        self._workers.clear()
        logger.info("Worker pool stopped")

    def pause(self):
        self._pause_event.set()

    def resume(self):
        self._pause_event.clear()

    def resize(self, target_workers: int):
        """Adjust pool size dynamically."""
        target = max(1, min(target_workers, self._max_workers))
        current = self.active_count

        if target > current:
            for _ in range(target - current):
                self._spawn_worker()
            logger.info("Workers added", from_=current, to=target)
        elif target < current:
            # Mark excess workers for shutdown (they'll exit after current task)
            excess = current - target
            for w in list(self._workers.values()):
                if excess <= 0:
                    break
                if w.is_alive:
                    w.is_alive = False
                    excess -= 1
            logger.info("Workers reduced", from_=current, to=target)

    def _spawn_worker(self):
        wid = self._next_id
        self._next_id += 1
        state = WorkerState(
            worker_id=wid,
            started_at=time.monotonic(),
            last_heartbeat=time.monotonic(),
        )
        state.task = asyncio.create_task(self._worker_loop(state))
        self._workers[wid] = state

    async def _worker_loop(self, state: WorkerState):
        """Main loop for a single worker."""
        logger.info("Worker started", worker_id=state.worker_id)

        try:
            while not self._stop_event.is_set() and state.is_alive:
                # Check pause
                while self._pause_event.is_set():
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(0.5)

                # Check recycle
                if self._recycle_after > 0 and state.uploads_completed >= self._recycle_after:
                    logger.info(
                        "Worker recycling after max uploads",
                        worker_id=state.worker_id,
                        uploads=state.uploads_completed,
                    )
                    break

                # Dequeue
                batch = await self._queue.dequeue_album(timeout=5.0)
                if batch is None:
                    state.last_heartbeat = time.monotonic()
                    continue

                # Process
                state.current_file = batch.items[0].filename if batch.items else ""
                state.last_heartbeat = time.monotonic()

                try:
                    result = await self._upload_fn(batch)
                    state.last_heartbeat = time.monotonic()

                    # result should be a dict: {file_id: bool, ...}
                    if result:
                        for item in batch.items:
                            if result.get(item.file_id, False):
                                state.uploads_completed += 1
                                self._queue.complete(item.file_id)
                            else:
                                state.uploads_failed += 1
                                self._queue.fail(item.file_id)
                    else:
                        # All failed
                        for item in batch.items:
                            state.uploads_failed += 1
                            self._queue.fail(item.file_id)

                    if self._on_batch_complete:
                        await self._on_batch_complete(batch, result)

                except Exception as e:
                    logger.error(
                        "Worker upload error",
                        worker_id=state.worker_id,
                        error=str(e),
                    )
                    for item in batch.items:
                        state.uploads_failed += 1
                        self._queue.fail(item.file_id)

                # Apply pacing delay
                if self._pacing_fn:
                    delay = self._pacing_fn()
                    if delay > 0:
                        await asyncio.sleep(delay)

                state.current_file = ""

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Worker crashed", worker_id=state.worker_id, error=str(e))
        finally:
            state.is_alive = False
            logger.info(
                "Worker stopped",
                worker_id=state.worker_id,
                completed=state.uploads_completed,
                failed=state.uploads_failed,
            )

            # Auto-respawn if pool is still running
            if self._running and not self._stop_event.is_set():
                self._spawn_worker()

    async def restart_all(self):
        """Force-restart all workers (for recovery)."""
        logger.warning("Force-restarting all workers")
        for w in list(self._workers.values()):
            w.is_alive = False
            if w.task:
                w.task.cancel()
        self._workers.clear()

        # Wait a moment then respawn
        await asyncio.sleep(1)
        for _ in range(self._initial_workers):
            self._spawn_worker()

    def check_stalled_workers(self, threshold_seconds: float = 300.0) -> List[int]:
        """Return IDs of workers that haven't heartbeated within threshold."""
        now = time.monotonic()
        stalled = []
        for w in self._workers.values():
            if w.is_alive and (now - w.last_heartbeat) > threshold_seconds:
                stalled.append(w.worker_id)
        return stalled
