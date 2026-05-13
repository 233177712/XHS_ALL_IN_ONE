from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from command_util import resolve_command


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BASELINE = ROOT / ".quality" / "ts-prune-baseline.txt"


def _normalize(line: str) -> str:
    normalized = line.strip().replace("\\", "/")
    return normalized.lstrip("/")


def _collect() -> list[str]:
    result = subprocess.run(
        [resolve_command("npx.cmd", "npx"), "ts-prune", "-p", "tsconfig.json"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode not in (0, 1):
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)

    items: list[str] = []
    for raw_line in result.stdout.splitlines():
        normalized = _normalize(raw_line)
        if not normalized or "(used in module)" in normalized:
            continue
        items.append(normalized)
    return sorted(set(items))


def _load_baseline() -> list[str]:
    if not BASELINE.exists():
        raise SystemExit(
            f"Missing baseline: {BASELINE}. Run `python scripts/update_quality_baselines.py` first."
        )
    return sorted({line.strip() for line in BASELINE.read_text(encoding="utf-8").splitlines() if line.strip()})


def _update() -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text("\n".join(_collect()) + "\n", encoding="utf-8")
    print(f"Updated {BASELINE}")


def main() -> None:
    if "--update" in sys.argv:
        _update()
        return

    current = set(_collect())
    baseline = set(_load_baseline())
    new_items = sorted(current - baseline)

    if new_items:
        print("New dead exports detected by ts-prune:")
        for item in new_items:
            print(f"  FAIL  {item}")
        raise SystemExit(1)

    removed = len(baseline - current)
    print(f"ts-prune baseline check passed (0 new, {removed} old issue(s) removed)")


if __name__ == "__main__":
    main()
