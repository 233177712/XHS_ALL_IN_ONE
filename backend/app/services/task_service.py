from __future__ import annotations

from backend.app.services.mock_data import sample_tasks


def list_tasks(platform: str | None = None) -> list[dict]:
    tasks = sample_tasks()
    if platform:
        return [task for task in tasks if task["platform"] == platform]
    return tasks
