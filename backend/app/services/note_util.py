from __future__ import annotations

import json
from typing import Any

from backend.app.models import MonitoringTarget, Note


def as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().lower().replace(",", "")
        multiplier = 1
        if cleaned.endswith("w"):
            multiplier = 10000
            cleaned = cleaned[:-1]
        try:
            return int(float(cleaned) * multiplier)
        except ValueError:
            return 0
    return 0


def _first_metric(raw: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in raw:
            return as_int(raw.get(key))
    return 0


METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "likes": ("likes", "liked_count", "like_count", "likedCount"),
    "collects": ("collects", "collected_count", "collect_count", "collectedCount"),
    "comments": ("comments", "comment_count", "commentCount"),
    "shares": ("shares", "share_count", "shareCount"),
}


def note_metrics(note: Note) -> dict[str, int]:
    raw = note.raw_json or {}
    interaction = raw.get("interact_info") if isinstance(raw.get("interact_info"), dict) else {}
    merged = {**raw, **interaction}
    result: dict[str, int] = {}
    for name, keys in METRIC_KEYS.items():
        result[name] = _first_metric(merged, keys)
    result["engagement"] = sum(result.values())
    return result


def note_haystack(note: Note) -> str:
    return "\n".join(
        [
            note.note_id or "",
            note.title or "",
            note.content or "",
            note.author_name or "",
            json.dumps(note.raw_json or {}, ensure_ascii=False),
        ]
    ).lower()


def note_matches_target(note: Note, target: MonitoringTarget) -> bool:
    needle = target.value.strip().lower()
    if not needle:
        return False
    return needle in note_haystack(note)


def note_matches_value(note: Note, value: str) -> bool:
    needle = value.strip().lower()
    if not needle:
        return False
    return needle in note_haystack(note)
