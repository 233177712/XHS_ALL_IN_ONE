from __future__ import annotations

import shutil


def resolve_command(*names: str) -> str:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(f"Cannot find executable. Tried: {', '.join(names)}")
