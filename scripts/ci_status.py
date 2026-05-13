from __future__ import annotations

from gh_actions_util import load_workflow_runs


def main() -> None:
    runs = load_workflow_runs(
        workflow="ci.yml",
        limit=5,
        fields="databaseId,displayTitle,headBranch,status,conclusion,createdAt,url",
    )
    if not runs:
        print("No CI runs found for ci.yml")
        return

    print("Latest GitHub Actions runs for ci.yml:")
    for run in runs:
        status = run.get("status") or "unknown"
        conclusion = run.get("conclusion") or "pending"
        print(
            f"  #{run['databaseId']}  {status:<10} {conclusion:<10} "
            f"[{run['headBranch']}] {run['displayTitle']}"
        )
        print(f"      {run['url']}")


if __name__ == "__main__":
    main()
