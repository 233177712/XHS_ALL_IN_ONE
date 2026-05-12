from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter
from backend.app.api.platforms.xhs.crawl import (
    _data_items,
    _save_normalized_notes,
)
from backend.app.api.platforms.xhs.pc import (
    _normalize_detail_payload,
    _normalize_search_item,
)
from backend.app.core.time import shanghai_now
from backend.app.models import (
    AccountCookieVersion,
    MonitoringSnapshot,
    MonitoringTarget,
    Note,
    PlatformAccount,
    Task,
    User,
)
from backend.app.services.notification_service import notify_target_paused


def _find_pc_account(db: Session, target: MonitoringTarget, user: User) -> PlatformAccount | None:
    if target.platform_account_id:
        account = db.get(PlatformAccount, target.platform_account_id)
        if account and account.user_id == user.id and account.sub_type == "pc" and account.status == "active":
            return account
    return db.scalars(
        select(PlatformAccount).where(
            PlatformAccount.user_id == user.id,
            PlatformAccount.platform == "xhs",
            PlatformAccount.sub_type == "pc",
            PlatformAccount.status == "active",
        ).limit(1)
    ).first()


def _decrypt_cookies(db: Session, account: PlatformAccount) -> str | None:
    from backend.app.core.security import decrypt_text

    version = db.scalars(
        select(AccountCookieVersion)
        .where(AccountCookieVersion.platform_account_id == account.id)
        .order_by(AccountCookieVersion.created_at.desc())
        .limit(1)
    ).first()
    if not version:
        return None
    raw = decrypt_text(version.encrypted_cookies)
    try:
        import json
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return "; ".join(f"{k}={v}" for k, v in parsed.items())
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "proxy" in msg or "connect" in msg or "timeout" in msg or "network" in msg:
        return "network"
    if "cookie" in msg or "auth" in msg or "login" in msg or "expired" in msg or "401" in msg:
        return "auth_expired"
    if "rate" in msg or "429" in msg or "频繁" in msg:
        return "rate_limit"
    return "adapter"


def _crawl_for_target(
    adapter: XhsPcApiAdapter,
    target: MonitoringTarget,
) -> tuple[bool, list[dict[str, Any]], str]:
    try:
        if target.target_type in ("keyword", "brand"):
            success, message, raw = adapter.search_note(target.value, page=1)
            if not success:
                return False, [], message or "search failed"
            items = [_normalize_search_item(item) for item in _data_items(raw)]
            return True, items, ""
        elif target.target_type == "account":
            success, message, raw = adapter.get_user_notes(target.value)
            if not success:
                return False, [], message or "user notes failed"
            items = [_normalize_search_item(item) for item in _data_items(raw)]
            return True, items, ""
        elif target.target_type == "note_url":
            success, message, raw = adapter.get_note_info(target.value)
            if not success:
                return False, [], message or "note detail failed"
            items = [_normalize_detail_payload(raw or {}, source_url=target.value)]
            return True, items, ""
        else:
            return False, [], f"unsupported target_type: {target.target_type}"
    except Exception as exc:
        return False, [], str(exc)


def _note_engagement(note: Note) -> int:
    raw = note.raw_json or {}
    return sum(
        int(raw.get(k, 0) or 0)
        for k in ("likes", "collects", "comments", "shares")
    )


def _make_snapshot(db: Session, target: MonitoringTarget, user: User) -> MonitoringSnapshot:
    notes = db.scalars(
        select(Note)
        .where(Note.user_id == user.id, Note.platform == "xhs")
        .order_by(Note.created_at.desc())
    ).all()

    from backend.app.api.platforms.xhs.monitoring import _serialize_monitoring_note
    from backend.app.services.note_util import note_matches_target
    matched = [n for n in notes if note_matches_target(n, target)]
    matched.sort(key=_note_engagement, reverse=True)

    payload = {
        "matched_count": len(matched),
        "total_engagement": sum(_note_engagement(n) for n in matched),
        "top_notes": [_serialize_monitoring_note(n) for n in matched[:10]],
    }
    snapshot = MonitoringSnapshot(target_id=target.id, payload=payload)
    db.add(snapshot)
    db.flush()
    return snapshot


def _snapshot_from_note_url(normalized_items: list[dict[str, Any]]) -> dict[str, Any]:
    if not normalized_items:
        return {"matched_count": 0, "total_engagement": 0, "likes": 0, "collects": 0, "comments": 0, "shares": 0, "top_notes": []}
    note = normalized_items[0]
    likes = int(note.get("likes") or 0)
    collects = int(note.get("collects") or 0)
    comments = int(note.get("comments") or 0)
    shares = int(note.get("shares") or 0)
    engagement = likes + collects + comments + shares
    return {
        "matched_count": 1,
        "total_engagement": engagement,
        "likes": likes,
        "collects": collects,
        "comments": comments,
        "shares": shares,
        "top_notes": [{
            "note_id": str(note.get("note_id") or ""),
            "title": str(note.get("title") or ""),
            "author_name": str(note.get("author_name") or ""),
            "likes": likes,
            "collects": collects,
            "comments": comments,
            "shares": shares,
            "engagement": engagement,
        }],
    }


def _check_viral_potential(db: Session, target: MonitoringTarget) -> dict[str, Any]:
    config = target.config or {}
    threshold = float(config.get("viral_threshold", 10))
    snapshots = db.scalars(
        select(MonitoringSnapshot)
        .where(MonitoringSnapshot.target_id == target.id)
        .order_by(MonitoringSnapshot.created_at.desc(), MonitoringSnapshot.id.desc())
        .limit(2)
    ).all()
    if len(snapshots) < 2:
        return {"is_viral": False, "velocity": 0, "reason": "insufficient_data"}
    latest = snapshots[0]
    previous = snapshots[1]
    latest_engagement = int((latest.payload or {}).get("total_engagement", 0) or 0)
    previous_engagement = int((previous.payload or {}).get("total_engagement", 0) or 0)
    delta_engagement = latest_engagement - previous_engagement
    if delta_engagement <= 0:
        return {"is_viral": False, "velocity": 0, "reason": "no_growth"}
    lt = latest.created_at.replace(tzinfo=None) if latest.created_at.tzinfo else latest.created_at
    pt = previous.created_at.replace(tzinfo=None) if previous.created_at.tzinfo else previous.created_at
    hours_elapsed = (lt - pt).total_seconds() / 3600
    if hours_elapsed <= 0:
        return {"is_viral": False, "velocity": 0, "reason": "no_time_elapsed"}
    velocity = delta_engagement / hours_elapsed
    if velocity >= threshold:
        return {"is_viral": True, "velocity": velocity, "reason": "viral_potential_detected"}
    return {"is_viral": False, "velocity": velocity, "reason": "below_threshold"}


def execute_monitoring_refresh(
    db: Session,
    target: MonitoringTarget,
    user: User,
    adapter_factory: Callable[[str], XhsPcApiAdapter] | None = None,
    check_rate_limit: bool = True,
) -> dict[str, Any]:
    from backend.app.api.platforms.xhs.monitoring import _serialize_snapshot, _serialize_target
    from backend.app.api.tasks import serialize_task

    now = shanghai_now()
    parent_task = Task(
        user_id=user.id,
        platform="xhs",
        task_type="monitoring_crawl",
        status="running",
        started_at=now,
        payload={"target_id": target.id, "target_type": target.target_type, "value": target.value},
    )
    db.add(parent_task)
    db.flush()

    account = _find_pc_account(db, target, user)
    if not account:
        parent_task.status = "failed"
        parent_task.finished_at = shanghai_now()
        parent_task.error_type = "validation"
        parent_task.payload = {**(parent_task.payload or {}), "error": "No active PC account"}
        target.consecutive_failures = target.consecutive_failures + 1
        target.last_crawl_error = "No active PC account"
        target.last_refreshed_at = now
        db.commit()
        db.refresh(target)
        db.refresh(parent_task)
        snapshot = _make_snapshot(db, target, user)
        db.commit()
        db.refresh(snapshot)
        return {"target": _serialize_target(target), "task": serialize_task(parent_task), "snapshot": _serialize_snapshot(snapshot)}

    cookies = _decrypt_cookies(db, account)
    if not cookies:
        parent_task.status = "failed"
        parent_task.finished_at = shanghai_now()
        parent_task.error_type = "auth_expired"
        parent_task.payload = {**(parent_task.payload or {}), "error": "No valid cookies"}
        target.consecutive_failures = target.consecutive_failures + 1
        target.last_crawl_error = "No valid cookies"
        target.last_refreshed_at = now
        db.commit()
        db.refresh(target)
        db.refresh(parent_task)
        snapshot = _make_snapshot(db, target, user)
        db.commit()
        db.refresh(snapshot)
        return {"target": _serialize_target(target), "task": serialize_task(parent_task), "snapshot": _serialize_snapshot(snapshot)}

    if check_rate_limit:
        from backend.app.services.rate_limiter import get_rate_limiter

        if not get_rate_limiter().allow(account.id):
            parent_task.status = "completed"
            parent_task.finished_at = shanghai_now()
            parent_task.payload = {**(parent_task.payload or {}), "skipped_rate_limit": True, "account_id": account.id}
            target.last_refreshed_at = now
            db.commit()
            db.refresh(target)
            db.refresh(parent_task)
            snapshot = _make_snapshot(db, target, user)
            db.commit()
            db.refresh(snapshot)
            return {"target": _serialize_target(target), "task": serialize_task(parent_task), "snapshot": _serialize_snapshot(snapshot)}

    factory = adapter_factory or (lambda c: XhsPcApiAdapter(c))
    adapter = factory(cookies)

    ok, normalized_items, error_msg = _crawl_for_target(adapter, target)

    target_config = target.config or {}
    is_note_url = target.target_type == "note_url"
    is_benchmark = is_note_url and target_config.get("benchmark_source")

    if ok and normalized_items and not is_benchmark:
        _save_normalized_notes(db, account, normalized_items)

    if is_note_url and ok and normalized_items:
        snapshot_payload = _snapshot_from_note_url(normalized_items)
        snapshot = MonitoringSnapshot(target_id=target.id, payload=snapshot_payload)
        db.add(snapshot)
        db.flush()
    else:
        snapshot = _make_snapshot(db, target, user)

    viral_hit = False
    viral_velocity = 0
    if is_benchmark:
        viral_result = _check_viral_potential(db, target)
        viral_velocity = viral_result["velocity"]
        if viral_result["is_viral"] and ok and normalized_items:
            _save_normalized_notes(db, account, normalized_items)
            viral_hit = True
            target.config = {
                **(target_config),
                "viral_potential": True,
                "viral_detected_at": shanghai_now().isoformat(),
                "viral_velocity": round(viral_velocity, 2),
            }

    if ok:
        parent_task.status = "completed"
        parent_task.finished_at = shanghai_now()
        parent_task.payload = {
            **(parent_task.payload or {}),
            "crawled_count": len(normalized_items),
            "snapshot_id": snapshot.id,
            "matched_count": (snapshot.payload or {}).get("matched_count", 0),
            "viral_potential": viral_hit,
            "viral_velocity": round(viral_velocity, 2) if viral_velocity else None,
        }
        target.consecutive_failures = 0
        target.last_crawl_error = None
    else:
        error_type = _classify_error(Exception(error_msg))
        parent_task.status = "failed"
        parent_task.finished_at = shanghai_now()
        parent_task.error_type = error_type
        parent_task.payload = {**(parent_task.payload or {}), "error": error_msg}
        target.consecutive_failures = target.consecutive_failures + 1
        target.last_crawl_error = error_msg
        if target.consecutive_failures >= 3:
            target.status = "paused"
            notify_target_paused(db, user.id, target.name, target.id)

    target.last_refreshed_at = shanghai_now()
    target.updated_at = target.last_refreshed_at
    db.commit()
    db.refresh(target)
    db.refresh(parent_task)
    db.refresh(snapshot)
    return {"target": _serialize_target(target), "task": serialize_task(parent_task), "snapshot": _serialize_snapshot(snapshot)}
