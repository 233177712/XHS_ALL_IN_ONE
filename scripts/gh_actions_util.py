from __future__ import annotations

import json
import subprocess
import sys

from command_util import resolve_command


def load_workflow_runs(*, workflow: str, limit: int, fields: str) -> list[dict]:
    command = [
        resolve_command("gh.exe", "gh"),
        "run",
        "list",
        "--workflow",
        workflow,
        "--limit",
        str(limit),
        "--json",
        fields,
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "could not find any workflows named" in stderr or "workflow ci.yml not found" in stderr:
            print(f"Workflow {workflow} is not available on GitHub yet. Push the workflow file first.")
            raise SystemExit(1)
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return json.loads(result.stdout)
