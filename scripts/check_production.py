"""Pre-deployment safety checks.

Run before deploying to production to catch common misconfigurations.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []
warnings: list[str] = []

# Check: default secret_key
yaml_path = ROOT / "config" / "default.yaml"
if yaml_path.exists():
    text = yaml_path.read_text(encoding="utf-8")
    if 'secret_key: "dev-only-change-me"' in text:
        errors.append(
            'config/default.yaml: secret_key is still the default "dev-only-change-me". '
            "Set SECRET_KEY env var or override in production.yaml."
        )

# Check: fernet_key not set in production
prod_yaml = ROOT / "config" / "production.yaml"
if prod_yaml.exists():
    text = prod_yaml.read_text(encoding="utf-8")
    if 'fernet_key: ""' in text:
        warnings.append(
            'config/production.yaml: fernet_key is empty. '
            "It will be derived from secret_key via SHA-256. "
            "Set FERNET_KEY explicitly for encryption isolation."
        )
    if 'secret_key: "CHANGE_ME_VIA_ENV_VAR"' in text:
        warnings.append(
            'config/production.yaml: secret_key is still the placeholder. '
            "Set SECRET_KEY env var before deploying."
        )

# Check: SQLite in production
from backend.app.core.config import get_settings

try:
    settings = get_settings()
    if "sqlite" in settings.database_url:
        warnings.append(
            f"Database is SQLite ({settings.database_url}). "
            "SQLite is not production-safe. Use MySQL for production."
        )
except Exception as exc:
    warnings.append(f"Cannot read settings: {exc}")

if errors:
    print("PRODUCTION CHECKS FAILED:")
    for e in errors:
        print(f"  ERROR  {e}")
    sys.exit(1)

if warnings:
    print("PRODUCTION CHECKS WITH WARNINGS:")
    for w in warnings:
        print(f"  WARN  {w}")

if not errors and not warnings:
    print("All production checks passed")
