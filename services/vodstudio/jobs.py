"""In-memory async job manager for VOD Studio slide-deck generation.

Deck generation runs for minutes (NotebookLM Studio is async), so the route
kicks off a job and the UI polls. Jobs are owner-scoped and live in process
memory — fine for a local single-instance app; they reset on restart.
"""

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lifecycle: pending -> running -> review (waiting for user) -> done
#                                \-> error (terminal)
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_REVIEW = "review"
STATUS_DONE = "done"
STATUS_ERROR = "error"


@dataclass
class Job:
    id: str
    owner: str
    params: Dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_PENDING
    stage: str = ""
    progress: float = 0.0           # 0..1
    logs: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    _task: Optional[asyncio.Task] = None

    # -- progress helpers passed to the orchestrator --
    def log(self, message: str) -> None:
        self.logs.append({"t": time.time(), "msg": message})
        self.updated = time.time()
        logger.info("[job %s] %s", self.id[:8], message)

    def set_stage(self, stage: str, progress: Optional[float] = None) -> None:
        self.stage = stage
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        self.updated = time.time()
        self.log(f"stage: {stage}")

    def to_public(self) -> Dict[str, Any]:
        """JSON-safe view for the polling endpoint (excludes the asyncio task)."""
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "logs": self.logs[-50:],
            "result": self.result,
            "params": {k: v for k, v in self.params.items() if k != "design_system"},
            "created": self.created,
            "updated": self.updated,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, owner: str, params: Dict[str, Any]) -> Job:
        job = Job(id=uuid.uuid4().hex, owner=owner, params=dict(params))
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str, owner: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.owner != owner:
            return None
        return job

    def list_for(self, owner: str) -> List[Job]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.owner == owner]
        return sorted(jobs, key=lambda j: j.created, reverse=True)

    def run(self, job: Job, coro_factory: Callable[[Job], Awaitable[None]]) -> None:
        """Launch the orchestrator coroutine as a background task with lifecycle
        bookkeeping. The coroutine sets status=review/done itself; this wrapper
        only forces error on an unhandled exception."""
        async def _wrap() -> None:
            job.status = STATUS_RUNNING
            job.updated = time.time()
            try:
                await coro_factory(job)
                # If the orchestrator didn't move it to a terminal/paused state,
                # default to done.
                if job.status == STATUS_RUNNING:
                    job.status = STATUS_DONE
            except Exception as e:  # noqa: BLE001 — surface any failure to the UI
                job.status = STATUS_ERROR
                job.error = str(e)
                job.log(f"ERROR: {e}")
                logger.exception("Job %s failed", job.id[:8])
            finally:
                job.updated = time.time()

        job._task = asyncio.create_task(_wrap())
