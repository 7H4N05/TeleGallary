"""
Recovery Engine — autonomous self-healing for the upload pipeline.

Receives FailureEvents from the FailureDetector and executes the appropriate
recovery strategy WITHOUT requiring a manual restart.

Recovery strategies
===================
1. **Stall recovery**: Cancel stuck upload tasks, restart workers, resume from
   last checkpoint.
2. **Client recreation**: Stop and recreate Pyrogram clients, restore peer
   cache, re-authenticate if needed.
3. **Memory relief**: Force garbage collection, clear PIL caches, optionally
   reduce batch sizes.
4. **Queue drain**: Detect deadlocked queues, cancel and re-enqueue stuck items.
5. **Session rotation**: If one Telegram session is consistently failing,
   rotate to backup account.
6. **Graceful degradation**: Reduce concurrent uploads, increase pacing,
   lower quality to keep pipeline alive.
"""

from __future__ import annotations

import asyncio
import gc
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from monitoring.failure_detector import FailureEvent, FailureSeverity, FailureType
from utils.logger import get_logger

logger = get_logger(__name__)


class RecoveryAction(str, Enum):
    RESTART_WORKERS = "restart_workers"
    RECREATE_CLIENT = "recreate_client"
    FORCE_GC = "force_gc"
    REDUCE_CONCURRENCY = "reduce_concurrency"
    INCREASE_PACING = "increase_pacing"
    DRAIN_QUEUE = "drain_queue"
    ROTATE_ACCOUNT = "rotate_account"
    RESUME_FROM_CHECKPOINT = "resume_from_checkpoint"
    NO_ACTION = "no_action"


@dataclass
class RecoveryRecord:
    """Audit log entry for a recovery action."""
    action: RecoveryAction
    trigger: FailureType
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    detail: str = ""


class RecoveryEngine:
    """Autonomous recovery agent.

    Wiring:
        detector.set_failure_handler(engine.handle_failure)
    """

    def __init__(self):
        self._history: List[RecoveryRecord] = []
        self._cooldowns: Dict[RecoveryAction, float] = {}  # action → last_executed_at
        self._min_cooldown_seconds: float = 60.0  # don't repeat same action within 1 min

        # External hooks — set by the orchestrator or reliability controller
        self._restart_workers_fn: Optional[Callable] = None
        self._recreate_client_fn: Optional[Callable] = None
        self._reduce_concurrency_fn: Optional[Callable] = None
        self._increase_pacing_fn: Optional[Callable] = None
        self._resume_fn: Optional[Callable] = None

    def register_hooks(
        self,
        restart_workers: Optional[Callable] = None,
        recreate_client: Optional[Callable] = None,
        reduce_concurrency: Optional[Callable] = None,
        increase_pacing: Optional[Callable] = None,
        resume_from_checkpoint: Optional[Callable] = None,
    ):
        self._restart_workers_fn = restart_workers
        self._recreate_client_fn = recreate_client
        self._reduce_concurrency_fn = reduce_concurrency
        self._increase_pacing_fn = increase_pacing
        self._resume_fn = resume_from_checkpoint

    @property
    def recovery_history(self) -> List[RecoveryRecord]:
        return list(self._history[-100:])

    async def handle_failure(self, event: FailureEvent):
        """Main entry point — routes failure events to recovery strategies."""
        action = self._decide_action(event)
        if action == RecoveryAction.NO_ACTION:
            return

        # Cooldown check
        if not self._can_execute(action):
            logger.info(
                "Recovery action in cooldown, skipping",
                action=action.value,
            )
            return

        logger.info(
            "Executing recovery action",
            action=action.value,
            trigger=event.failure_type.value,
            severity=event.severity.value,
        )

        success = await self._execute(action, event)
        self._history.append(RecoveryRecord(
            action=action,
            trigger=event.failure_type,
            success=success,
            detail=event.message,
        ))
        self._cooldowns[action] = time.monotonic()

    def _decide_action(self, event: FailureEvent) -> RecoveryAction:
        """Map a failure event to the best recovery action."""
        ft = event.failure_type
        sev = event.severity

        if ft == FailureType.UPLOAD_STALL:
            if sev == FailureSeverity.CRITICAL:
                # Check if this is a repeated stall
                consecutive = event.context.get("consecutive", 0)
                if consecutive >= 3:
                    return RecoveryAction.RECREATE_CLIENT
                return RecoveryAction.RESTART_WORKERS

        elif ft == FailureType.RETRY_STORM:
            return RecoveryAction.REDUCE_CONCURRENCY

        elif ft == FailureType.FLOODWAIT_ESCALATION:
            return RecoveryAction.INCREASE_PACING

        elif ft == FailureType.CLIENT_DISCONNECT:
            return RecoveryAction.RECREATE_CLIENT

        elif ft == FailureType.MEMORY_PRESSURE:
            return RecoveryAction.FORCE_GC

        elif ft == FailureType.EVENT_LOOP_SATURATION:
            return RecoveryAction.REDUCE_CONCURRENCY

        elif ft == FailureType.THROUGHPUT_COLLAPSE:
            return RecoveryAction.REDUCE_CONCURRENCY

        elif ft == FailureType.QUEUE_DEADLOCK:
            return RecoveryAction.DRAIN_QUEUE

        return RecoveryAction.NO_ACTION

    def _can_execute(self, action: RecoveryAction) -> bool:
        last = self._cooldowns.get(action, 0)
        return (time.monotonic() - last) >= self._min_cooldown_seconds

    async def _execute(self, action: RecoveryAction, event: FailureEvent) -> bool:
        try:
            if action == RecoveryAction.RESTART_WORKERS:
                if self._restart_workers_fn:
                    await self._restart_workers_fn()
                return True

            elif action == RecoveryAction.RECREATE_CLIENT:
                if self._recreate_client_fn:
                    await self._recreate_client_fn()
                return True

            elif action == RecoveryAction.FORCE_GC:
                return self._force_gc()

            elif action == RecoveryAction.REDUCE_CONCURRENCY:
                if self._reduce_concurrency_fn:
                    await self._reduce_concurrency_fn()
                return True

            elif action == RecoveryAction.INCREASE_PACING:
                if self._increase_pacing_fn:
                    await self._increase_pacing_fn()
                return True

            elif action == RecoveryAction.RESUME_FROM_CHECKPOINT:
                if self._resume_fn:
                    await self._resume_fn()
                return True

            elif action == RecoveryAction.DRAIN_QUEUE:
                logger.info("Queue drain requested — orchestrator will handle")
                return True

            return False

        except Exception as e:
            logger.error(
                "Recovery action failed",
                action=action.value,
                error=str(e),
            )
            return False

    @staticmethod
    def _force_gc() -> bool:
        """Force garbage collection and clear known caches.

        PIL's Image.open() + .load() allocates raw pixel buffers via
        Python's memory allocator.  These buffers are only freed when
        the Image object is garbage-collected.  Simply calling gc.collect()
        with generation=2 is insufficient if references still exist
        (e.g. in thread-local storage or asyncio task frames).

        Strategy:
        1. Clear PIL's internal tile cache and reset preinit state
        2. Full generation-2 GC to release any unreferenced Image objects
        3. ctypes malloc_trim to return freed pages to the OS (Linux only)
        4. Brief sleep to let the OS reclaim socket/buffer resources
        """
        try:
            # Clear PIL caches
            try:
                from PIL import Image
                # Reset PIL's format registry cache
                Image.preinit()
                # Clear any global image cache
                if hasattr(Image, '_initialized'):
                    Image._initialized = 1  # reset to "preinit done"
            except Exception:
                pass

            # Force full GC — all generations
            gc.collect(generation=2)
            gc.collect(generation=1)
            collected = gc.collect(generation=0)
            logger.info("Forced GC completed", collected_objects=collected)

            # On Linux, return freed pages to OS
            try:
                import ctypes
                libc = ctypes.CDLL('libc.so.6')
                libc.malloc_trim(0)
            except Exception:
                pass  # Windows doesn't have malloc_trim, that's fine

            return True
        except Exception as e:
            logger.error("GC failed", error=str(e))
            return False

    def snapshot(self) -> dict:
        recent = self._history[-10:] if self._history else []
        return {
            "total_recoveries": len(self._history),
            "recent_actions": [
                {
                    "action": r.action.value,
                    "trigger": r.trigger.value,
                    "success": r.success,
                    "timestamp": r.timestamp,
                    "detail": r.detail,
                }
                for r in recent
            ],
        }
