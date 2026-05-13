from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from command_util import resolve_command


ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / ".quality" / "jscpd-baseline.json"
TARGETS = ["backend/app", "frontend/src", "scripts"]


def _normalize_fragment(fragment: str) -> str:
    return " ".join(fragment.split())


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _entry_key(entry: dict) -> str:
    first = _normalize_path(entry["firstFile"]["name"])
    second = _normalize_path(entry["secondFile"]["name"])
    file_a, file_b = sorted((first, second))
    digest = hashlib.sha256(_normalize_fragment(entry["fragment"]).encode("utf-8")).hexdigest()
    return f"{entry['format']}|{digest}|{file_a}|{file_b}"


def _entry_description(entry: dict) -> str:
    first = _normalize_path(entry["firstFile"]["name"])
    second = _normalize_path(entry["secondFile"]["name"])
    return f"{entry['format']}: {first}:{entry['firstFile']['start']} <-> {second}:{entry['secondFile']['start']} ({entry['lines']} lines)"


def _collect() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="jscpd-baseline-") as temp_dir:
        command = [
            resolve_command("npx.cmd", "npx"),
            "jscpd",
            *TARGETS,
            "--config",
            str(ROOT / ".jscpd.json"),
            "--reporters",
            "json",
            "--output",
            temp_dir,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode not in (0, 1):
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit(result.returncode)

        report_path = Path(temp_dir) / "jscpd-report.json"
        if not report_path.exists():
            raise SystemExit("jscpd did not produce jscpd-report.json")

        report = json.loads(report_path.read_text(encoding="utf-8"))
        entries: dict[str, str] = {}
        for duplicate in report.get("duplicates", []):
            entries[_entry_key(duplicate)] = _entry_description(duplicate)
        return entries


def _load_baseline() -> dict[str, str]:
    if not BASELINE.exists():
        raise SystemExit(
            f"Missing baseline: {BASELINE}. Run `python scripts/update_quality_baselines.py` first."
        )
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {entry["key"]: entry["description"] for entry in payload.get("entries", [])}


def _update() -> None:
    current = _collect()
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [{"key": key, "description": current[key]} for key in sorted(current)]}
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {BASELINE}")


def main() -> None:
    if "--update" in sys.argv:
        _update()
        return

    current = _collect()
    baseline = _load_baseline()
    new_keys = sorted(set(current) - set(baseline))

    if new_keys:
        print("New duplicate blocks detected by jscpd:")
        for key in new_keys:
            print(f"  FAIL  {current[key]}")
        raise SystemExit(1)

    removed = len(set(baseline) - set(current))
    print(f"jscpd baseline check passed (0 new, {removed} old duplicate(s) removed)")


if __name__ == "__main__":
    main()
