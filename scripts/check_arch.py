"""Architecture boundary validation for XHS_ALL_IN_ONE.

Checks enforced by AGENTS.md layering rules:
  - services/ must not import from api/
  - adapters/ must not import from api/ or services/
  - pages/ must not import axios directly

Known violations (pre-existing tech debt) are allowed with a warning.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []
warnings: list[str] = []

# Known pre-existing violations (allowed but flagged as warnings)
KNOWN_VIOLATIONS: set[str] = {
    "backend/app/services/monitoring_crawl_service.py",
    "backend/app/services/scheduler_service.py",
}


def find_imports(directory: str, banned_pattern: str, label: str) -> None:
    base = ROOT / directory
    if not base.exists():
        return
    pattern = re.compile(banned_pattern)
    for path in sorted(base.rglob("*.py")):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if pattern.search(stripped):
                rel_str = str(rel.as_posix())
                entry = f"{rel_str}: {stripped}"
                if rel_str in KNOWN_VIOLATIONS:
                    warnings.append(entry)
                else:
                    errors.append(entry)


def find_ts_imports(directory: str, banned_pattern: str, label: str) -> None:
    base = ROOT / directory
    if not base.exists():
        return
    pattern = re.compile(banned_pattern)
    extensions = ("*.ts", "*.tsx")
    for ext in extensions:
        for path in sorted(base.rglob(ext)):
            if "node_modules" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if pattern.search(stripped):
                    rel = path.relative_to(ROOT)
                    errors.append(f"{rel}: {stripped}")


find_imports("backend/app/services", r'from\s+backend\.app\.api', "services imports api")
find_imports("backend/app/adapters", r'from\s+backend\.app\.(api|services)', "adapters imports api/services")
find_ts_imports("frontend/src/pages", r'from\s+["\']axios["\']', "pages imports axios")

for w in warnings:
    print(f"  WARN  (pre-existing) {w}")
for e in errors:
    print(f"  FAIL  {e}")

if errors:
    print(f"\n{len(errors)} new violation(s). Fix before committing.")
    sys.exit(1)
else:
    print(f"Architecture checks passed ({len(warnings)} known pre-existing, 0 new)")
