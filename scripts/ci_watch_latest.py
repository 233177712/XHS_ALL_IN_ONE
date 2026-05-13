from __future__ import annotations

import subprocess

from command_util import resolve_command
from gh_actions_util import load_workflow_runs


def _latest_run_id() -> str:
    runs = load_workflow_runs(
        workflow="ci.yml",
        limit=1,
        fields="databaseId,displayTitle,status,conclusion,url",
    )
    if not runs:
        raise SystemExit("No CI runs found for ci.yml")

    run = runs[0]
    print(
        f"Watching CI run #{run['databaseId']} ({run['displayTitle']}) - "
        f"status={run.get('status')}, conclusion={run.get('conclusion') or 'pending'}"
    )
    print(run["url"])
    return str(run["databaseId"])


def main() -> None:
    run_id = _latest_run_id()
    result = subprocess.run([resolve_command("gh.exe", "gh"), "run", "watch", run_id, "--exit-status"])
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
