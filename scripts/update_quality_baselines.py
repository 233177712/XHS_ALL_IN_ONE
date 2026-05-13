from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _run(script_name: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), "--update"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def main() -> None:
    _run("check_ts_prune_baseline.py")
    _run("check_jscpd_baseline.py")


if __name__ == "__main__":
    main()
