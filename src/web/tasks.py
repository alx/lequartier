from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE    = "done"
    ERROR   = "error"


@dataclass
class TaskState:
    task_id:      str
    status:       Status = Status.PENDING
    progress:     str    = ""
    progress_pct: int    = 0
    result:       dict   = field(default_factory=dict)
    error:        str    = ""
    created_at:   float  = field(default_factory=time.time)


class TaskStore:
    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._tasks: dict[str, TaskState] = {}

    def create(self) -> TaskState:
        t = TaskState(task_id=str(uuid.uuid4()))
        with self._lock:
            self._tasks[t.task_id] = t
        return t

    def get(self, task_id: str) -> TaskState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **kwargs) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t:
                for k, v in kwargs.items():
                    setattr(t, k, v)

    def cleanup_old(self, max_age_hours: int = 24) -> None:
        cutoff = time.time() - max_age_hours * 3600
        with self._lock:
            stale = [tid for tid, t in self._tasks.items() if t.created_at < cutoff]
            for tid in stale:
                del self._tasks[tid]


store = TaskStore()


def run_in_thread(fn, *args, **kwargs) -> TaskState:
    task = store.create()
    t = threading.Thread(target=fn, args=(task, *args), **kwargs, daemon=True)
    t.start()
    return task
