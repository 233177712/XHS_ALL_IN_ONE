from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.core.time import shanghai_now


@dataclass
class TaskState:
    id: str
    status: str
    progress: int
    updated_at: datetime


class InProcessTaskRunner:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def register(self, task_id: str) -> TaskState:
        state = TaskState(task_id, "pending", 0, shanghai_now())
        self._tasks[task_id] = state
        return state

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)


task_runner = InProcessTaskRunner()
