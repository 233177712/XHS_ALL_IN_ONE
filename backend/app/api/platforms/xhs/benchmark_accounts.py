from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from statistics import quantiles
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.adapters.xhs.pc_api_adapter import XhsPcApiAdapter
from backend.app.api.platforms.xhs.crawl import (
    MAX_XHS_CRAWL_INTERVAL_SECONDS,
    DataCrawlRequest,
    _coerce_timestamp_seconds,
    _save_normalized_notes,
    _sse_event,
    crawl_user_note_links,
    iter_data_crawl_events,
)
from backend.app.api.platforms.xhs.monitoring import _serialize_target
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import SHANGHAI_TZ, shanghai_now
from backend.app.models import MonitoringTarget, PlatformAccount, User
from backend.app.schemas.common import paginated_query
from backend.app.services.monitoring_crawl_service import _decrypt_cookies

router = APIRouter(prefix="/xhs/benchmark-accounts", tags=["xhs-benchmark-accounts"])


class BenchmarkAccountCreateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=512)


class CrawlPopularNotesRequest(BaseModel):
    account_id: int = Field(ge=1)
    recent_months: int = Field(default=1, ge=1, le=24)
    max_notes: int = Field(default=20, ge=1, le=200)
    request_interval_seconds: float = Field(default=1, ge=0, le=MAX_XHS_CRAWL_INTERVAL_SECONDS)


class ScanAndMonitorRequest(BaseModel):
    account_id: int = Field(ge=1)
    recent_hours: int = Field(default=168, ge=1, le=720)
    crawl_interval_minutes: int = Field(default=60, ge=10, le=1440)
    request_interval_seconds: float = Field(default=1, ge=0, le=MAX_XHS_CRAWL_INTERVAL_SECONDS)


class AutoScanConfigRequest(BaseModel):
    enabled: bool
    scan_interval_hours: int = Field(default=6, ge=1, le=168)
    recent_hours: int = Field(default=168, ge=1, le=720)
    crawl_interval_minutes: int = Field(default=60, ge=10, le=1440)
    account_id: int = Field(ge=1)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _config_profile(target: MonitoringTarget) -> dict[str, Any]:
    config = target.config if isinstance(target.config, dict) else {}
    profile = config.get("profile") if isinstance(config, dict) else None
    return profile if isinstance(profile, dict) else {}


def _profile_is_complete(target: MonitoringTarget) -> bool:
    profile = _config_profile(target)
    return bool(profile.get("fetched_at") or profile.get("avatar_url") or profile.get("followers") or profile.get("note_count") or profile.get("nickname"))


def _build_interaction_counts(interactions: Any) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    if not isinstance(interactions, list):
        return counts
    for item in interactions:
        if not isinstance(item, dict):
            continue
        value = _first_present(item.get("i18n_count"), item.get("count"))
        interaction_type = str(item.get("type") or "").strip().lower()
        interaction_name = str(item.get("name") or "").strip().lower()
        if interaction_type:
            counts[interaction_type] = value
        if interaction_name:
            counts[interaction_name] = value
    return counts


def _extract_benchmark_profile(raw_payload: dict[str, Any], user_id: str) -> dict[str, Any]:
    data = raw_payload.get("data") if isinstance(raw_payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    basic_info = data.get("basic_info") if isinstance(data.get("basic_info"), dict) else {}
    interaction_counts = _build_interaction_counts(data.get("interactions"))

    return {
        "user_id": user_id,
        "nickname": _first_present(basic_info.get("nickname"), data.get("nickname"), ""),
        "avatar_url": _first_present(basic_info.get("imageb"), basic_info.get("images"), data.get("avatar"), ""),
        "red_id": _first_present(basic_info.get("red_id"), data.get("red_id"), ""),
        "description": _first_present(basic_info.get("desc"), data.get("desc"), ""),
        "ip_location": _first_present(basic_info.get("ip_location"), data.get("ip_location"), ""),
        "followers": _first_present(
            interaction_counts.get("fans"),
            interaction_counts.get("粉丝"),
            basic_info.get("fans"),
            data.get("fans"),
            data.get("follower_count"),
        ),
        "following": _first_present(
            interaction_counts.get("follows"),
            interaction_counts.get("关注"),
            basic_info.get("follows"),
            data.get("follows"),
            data.get("following_count"),
        ),
        "likes": _first_present(
            interaction_counts.get("interaction"),
            interaction_counts.get("获赞与收藏"),
            data.get("likes"),
            data.get("liked_count"),
        ),
        "note_count": _first_present(
            interaction_counts.get("notes"),
            interaction_counts.get("note"),
            interaction_counts.get("posts"),
            interaction_counts.get("post"),
            interaction_counts.get("笔记"),
            basic_info.get("note_count"),
            basic_info.get("notes_count"),
            data.get("note_count"),
            data.get("notes_count"),
            data.get("post_count"),
        ),
        "fetched_at": shanghai_now().isoformat(),
    }


def _refresh_target_profile(db: Session | None, target: MonitoringTarget, account: PlatformAccount, *, adapter: XhsPcApiAdapter | None = None) -> MonitoringTarget:
    normalized_url, user_id = _normalize_user_profile_url(target.value)
    try:
        if adapter is not None:
            success, message, raw_payload = adapter.get_user_profile(normalized_url)
        else:
            cookies = _decrypt_cookies(db, account) if db else None
            if not cookies:
                return target
            success, message, raw_payload = XhsPcApiAdapter(cookies).get_user_profile(normalized_url)
    except Exception:
        return target
    if not success or not isinstance(raw_payload, dict):
        return target

    profile = _extract_benchmark_profile(raw_payload, user_id)
    target.value = normalized_url
    target.config = {
        **(target.config or {}),
        "profile": profile,
    }
    target.updated_at = shanghai_now()
    if db is not None:
        db.flush()
    return target


def _normalize_user_profile_url(raw_url: str) -> tuple[str, str]:
    candidate = raw_url.strip()
    if not candidate:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请输入小红书用户主页链接")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    host = parsed.netloc.lower()
    if "xiaohongshu.com" not in host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅支持小红书用户主页链接")

    path = parsed.path.rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 3 or segments[0] != "user" or segments[1] != "profile":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请输入正确的小红书用户主页链接")

    user_id = segments[2].strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无法识别小红书用户 ID")

    normalized_url = urlunparse((parsed.scheme or "https", parsed.netloc, f"/user/profile/{user_id}", "", parsed.query, ""))
    return normalized_url, user_id


def _get_owned_benchmark_target(db: Session, current_user: User, target_id: int) -> MonitoringTarget:
    target = db.get(MonitoringTarget, target_id)
    if target is None or target.user_id != current_user.id or target.platform != "xhs" or target.target_type != "account":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Benchmark account not found")
    return target


def _get_owned_pc_account(db: Session, current_user: User, account_id: int) -> PlatformAccount:
    account = db.get(PlatformAccount, account_id)
    if (
        account is None
        or account.user_id != current_user.id
        or account.platform != "xhs"
        or account.sub_type != "pc"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PC account not found")
    if account.status != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请选择一个可用的 PC 账号")
    return account


def _find_active_pc_account(db: Session, current_user: User, account_id: int | None = None) -> PlatformAccount | None:
    if account_id:
        account = db.get(PlatformAccount, account_id)
        if (
            account is not None
            and account.user_id == current_user.id
            and account.platform == "xhs"
            and account.sub_type == "pc"
            and account.status == "active"
        ):
            return account
    return db.scalars(
        select(PlatformAccount).where(
            PlatformAccount.user_id == current_user.id,
            PlatformAccount.platform == "xhs",
            PlatformAccount.sub_type == "pc",
            PlatformAccount.status == "active",
        ).limit(1)
    ).first()


def _note_engagement(note: dict[str, Any]) -> int:
    return int(note.get("likes") or 0) + int(note.get("collects") or 0) + int(note.get("comments") or 0) + int(note.get("shares") or 0)


def _note_publish_days(note: dict[str, Any], now_s: int | None = None) -> float:
    ts = _coerce_timestamp_seconds(note.get("timestamp"))
    if ts is None or ts <= 0:
        return 1.0
    if now_s is None:
        now_s = int(datetime.now(SHANGHAI_TZ).timestamp())
    return max((now_s - ts) / 86400, 1)


def _note_daily_rate(note: dict[str, Any], now_s: int | None = None) -> float:
    return _note_engagement(note) / _note_publish_days(note, now_s=now_s)


def _pick_popular_notes(notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float, float]:
    if not notes:
        return [], 0, 0

    now_s = int(datetime.now(SHANGHAI_TZ).timestamp())
    with_rate = [(note, _note_daily_rate(note, now_s=now_s)) for note in notes]

    ranked = sorted(with_rate, key=lambda pair: pair[1], reverse=True)
    rates = sorted(pair[1] for pair in with_rate)

    q1, _med, q3 = quantiles(rates, n=4)
    iqr = q3 - q1

    mild_threshold = q3 + 1.5 * iqr
    extreme_threshold = q3 + 3.0 * iqr
    floor_rate = max(50.0, q3 * 1.5)

    final_threshold = max(floor_rate, mild_threshold)

    popular_pairs = [
        pair for pair in ranked
        if _note_engagement(pair[0]) > 0 and pair[1] >= final_threshold
    ]

    if not popular_pairs and extreme_threshold > mild_threshold:
        relaxed_threshold = min(extreme_threshold, floor_rate)
        popular_pairs = [
            pair for pair in ranked
            if _note_engagement(pair[0]) > 0 and pair[1] >= relaxed_threshold
        ]
        if len(popular_pairs) > 2:
            popular_pairs = popular_pairs[:2]

    return [pair[0] for pair in popular_pairs], final_threshold, extreme_threshold


def _serialize_popular_note(note: dict[str, Any], baseline_daily_rate: float) -> dict[str, Any]:
    engagement = _note_engagement(note)
    timestamp = _coerce_timestamp_seconds(note.get("timestamp"))
    rate = _note_daily_rate(note)
    multiplier = round(rate / baseline_daily_rate, 2) if baseline_daily_rate > 0 else None
    return {
        "note_id": str(note.get("note_id") or ""),
        "note_url": str(note.get("note_url") or ""),
        "title": str(note.get("title") or ""),
        "author_name": str(note.get("author_name") or ""),
        "cover_url": str(note.get("cover_url") or ""),
        "likes": int(note.get("likes") or 0),
        "collects": int(note.get("collects") or 0),
        "comments": int(note.get("comments") or 0),
        "shares": int(note.get("shares") or 0),
        "engagement": engagement,
        "multiplier": multiplier,
        "timestamp": timestamp,
    }


@router.get("")
def list_benchmark_accounts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    all_rows = db.scalars(
        select(MonitoringTarget)
        .where(
            MonitoringTarget.user_id == current_user.id,
            MonitoringTarget.platform == "xhs",
            MonitoringTarget.target_type == "account",
        )
        .order_by(MonitoringTarget.created_at.desc(), MonitoringTarget.id.desc())
    ).all()
    mutated = False
    for target in all_rows:
        if _profile_is_complete(target):
            continue
        account = _find_active_pc_account(db, current_user, target.platform_account_id)
        if account is None:
            continue
        _refresh_target_profile(db, target, account)
        mutated = True
    if mutated:
        db.commit()
        for target in all_rows:
            db.refresh(target)

    monitored_counts: dict[int, int] = {}
    all_note_urls = db.scalars(
        select(MonitoringTarget).where(
            MonitoringTarget.user_id == current_user.id,
            MonitoringTarget.platform == "xhs",
            MonitoringTarget.target_type == "note_url",
        )
    ).all()
    for note_url_target in all_note_urls:
        cfg = note_url_target.config or {}
        source_id = cfg.get("source_benchmark_id")
        if isinstance(source_id, int) and source_id > 0:
            monitored_counts[source_id] = monitored_counts.get(source_id, 0) + 1

    statement = (
        select(MonitoringTarget)
        .where(
            MonitoringTarget.user_id == current_user.id,
            MonitoringTarget.platform == "xhs",
            MonitoringTarget.target_type == "account",
        )
        .order_by(MonitoringTarget.created_at.desc(), MonitoringTarget.id.desc())
    )
    result = paginated_query(db, statement, page=page, page_size=page_size, map_item=_serialize_target)
    for item in result["items"]:
        tid = item.get("id")
        if isinstance(tid, int):
            item["monitored_note_count"] = monitored_counts.get(tid, 0)
    return result


@router.post("")
def create_benchmark_account(
    payload: BenchmarkAccountCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized_url, user_id = _normalize_user_profile_url(payload.url)
    existing = db.scalars(
        select(MonitoringTarget).where(
            MonitoringTarget.user_id == current_user.id,
            MonitoringTarget.platform == "xhs",
            MonitoringTarget.target_type == "account",
            MonitoringTarget.name == user_id,
        )
    ).first()
    if existing is not None:
        return _serialize_target(existing)

    target = MonitoringTarget(
        user_id=current_user.id,
        platform="xhs",
        target_type="account",
        name=user_id,
        value=normalized_url,
        status="active",
        config={},
    )
    db.add(target)
    account = _find_active_pc_account(db, current_user)
    if account is not None:
        target.platform_account_id = account.id
        _refresh_target_profile(db, target, account)
    db.commit()
    db.refresh(target)
    return _serialize_target(target)


@router.delete("/{target_id}")
def delete_benchmark_account(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_benchmark_target(db, current_user, target_id)
    db.delete(target)
    db.commit()
    return {"id": target_id, "status": "deleted"}


@router.post("/{target_id}/crawl-popular")
def crawl_popular_notes(
    target_id: int,
    payload: CrawlPopularNotesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_benchmark_target(db, current_user, target_id)
    account = _get_owned_pc_account(db, current_user, payload.account_id)

    cookies = _decrypt_cookies(db, account)
    if not cookies:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前 PC 账号没有可用 cookies")

    adapter = XhsPcApiAdapter(cookies)

    def generate() -> Generator[str, None, None]:
        yield _sse_event({"type": "progress", "message": "正在抓取对标账号笔记列表..."})
        try:
            note_links = crawl_user_note_links(
                adapter, target.value,
                recent_months=payload.recent_months,
                max_notes=payload.max_notes,
                time_sleep=1,
            )
        except Exception as exc:
            yield _sse_event({"type": "error", "message": f"笔记列表抓取失败: {exc}"})
            return

        if not note_links:
            yield _sse_event({"type": "error", "message": "未抓取到笔记链接"})
            return

        yield _sse_event({"type": "progress", "message": f"已获取 {len(note_links)} 条笔记链接，开始抓取详情..."})

        crawl_event_stream = iter_data_crawl_events(
            adapter,
            DataCrawlRequest(
                account_id=account.id, mode="note_urls",
                urls=[str(link.get("note_url") or "") for link in note_links if link.get("note_url")],
                max_notes=payload.max_notes, time_sleep=payload.request_interval_seconds, fetch_comments=False,
            ),
        )
        error_occurred = False
        crawl_result = None
        while True:
            try:
                event = next(crawl_event_stream)
                if event["type"] == "item" and event["item"].get("status") == "failed":
                    error_occurred = True
                yield _sse_event(event)
            except StopIteration as stop:
                crawl_result = stop.value
                break
        if crawl_result is None:
            crawl_result = {"items": [], "normalized_items": [], "success_count": 0, "failed_count": 0, "error_occurred": True, "error_message": "unknown"}

        normalized_notes = crawl_result.get("normalized_items") or []
        if error_occurred and not normalized_notes:
            yield _sse_event({"type": "error", "message": "笔记详情抓取失败"})
            return

        yield _sse_event({"type": "progress", "message": "正在分析爆款笔记..."})

        popular_notes, baseline_engagement, threshold_engagement = _pick_popular_notes(normalized_notes)

        saved_notes = _save_normalized_notes(db, account, popular_notes) if popular_notes else []

        target.config = {
            **(target.config or {}),
            "crawler_account_id": account.id,
            "recent_months": payload.recent_months,
            "max_notes": payload.max_notes,
            "request_interval_seconds": payload.request_interval_seconds,
        }
        target.platform_account_id = account.id
        target.last_refreshed_at = shanghai_now()
        target.updated_at = target.last_refreshed_at
        db.commit()
        db.refresh(target)

        _refresh_target_profile(None, target, account, adapter=adapter)

        success_count = int(crawl_result.get("success_count", 0))
        failed_count = int(crawl_result.get("failed_count", 0))

        yield _sse_event({
            "type": "done",
            "candidate_count": len(note_links),
            "crawled_count": success_count,
            "failed_count": failed_count,
            "popular_count": len(popular_notes),
            "imported_count": len(saved_notes),
            "baseline_engagement": baseline_engagement,
            "threshold_engagement": threshold_engagement,
            "items": [_serialize_popular_note(note, baseline_engagement) for note in popular_notes],
        })

    return StreamingResponse(generate(), media_type="text/event-stream")


def _existing_monitoring_urls(db: Session, user: User) -> set[str]:
    rows = db.scalars(
        select(MonitoringTarget.value).where(
            MonitoringTarget.user_id == user.id,
            MonitoringTarget.platform == "xhs",
            MonitoringTarget.target_type == "note_url",
        )
    ).all()
    return {str(row).strip().rstrip("/") for row in rows if row}


def _create_monitoring_target_for_note(
    db: Session,
    user: User,
    account: PlatformAccount,
    note_url: str,
    note_id: str,
    crawl_interval_minutes: int,
    *,
    source_benchmark_id: int | None = None,
) -> MonitoringTarget:
    target = MonitoringTarget(
        user_id=user.id,
        platform="xhs",
        target_type="note_url",
        name=note_id or note_url,
        value=note_url,
        status="active",
        crawl_interval_minutes=crawl_interval_minutes,
        platform_account_id=account.id,
        config={
            "benchmark_source": True,
            "viral_threshold": 10,
            **({"source_benchmark_id": source_benchmark_id} if source_benchmark_id is not None else {}),
        },
    )
    db.add(target)
    db.flush()
    return target


@router.post("/{target_id}/scan-and-monitor")
def scan_and_monitor(
    target_id: int,
    payload: ScanAndMonitorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_benchmark_target(db, current_user, target_id)
    account = _get_owned_pc_account(db, current_user, payload.account_id)
    cookies = _decrypt_cookies(db, account)
    if not cookies:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前 PC 账号没有可用 cookies")

    adapter = XhsPcApiAdapter(cookies)
    try:
        note_links = crawl_user_note_links(
            adapter,
            target.value,
            recent_hours=payload.recent_hours,
            max_notes=payload.recent_hours,
            time_sleep=payload.request_interval_seconds,
        )
    except Exception:
        note_links = []

    existing = _existing_monitoring_urls(db, current_user)
    new_targets: list[MonitoringTarget] = []
    for link in note_links:
        note_url = str(link.get("note_url") or "").strip().rstrip("/")
        note_id = str(link.get("note_id") or "").strip()
        if not note_url or not note_id:
            continue
        if note_url in existing:
            continue
        existing.add(note_url)
        _create_monitoring_target_for_note(
            db, current_user, account, note_url, note_id, payload.crawl_interval_minutes,
            source_benchmark_id=target.id,
        )
        new_targets.append(True)

    if new_targets:
        db.commit()

    target.config = {
        **(target.config or {}),
        "last_scan_account_id": account.id,
        "last_scan_at": shanghai_now().isoformat(),
        "last_scan_hours": payload.recent_hours,
        "last_scan_new_targets": len(new_targets),
    }
    target.updated_at = shanghai_now()
    db.commit()
    db.refresh(target)

    return {
        "target": _serialize_target(target),
        "scanned_links": len(note_links),
        "new_monitoring_targets": len(new_targets),
    }


@router.post("/{target_id}/auto-scan-config")
def configure_auto_scan(
    target_id: int,
    payload: AutoScanConfigRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_benchmark_target(db, current_user, target_id)
    _get_owned_pc_account(db, current_user, payload.account_id)
    from datetime import timedelta
    now = shanghai_now()
    target.config = {
        **(target.config or {}),
        "scan_enabled": payload.enabled,
        "scan_interval_hours": payload.scan_interval_hours,
        "scan_recent_hours": payload.recent_hours,
        "scan_crawl_interval_minutes": payload.crawl_interval_minutes,
        "scan_account_id": payload.account_id,
        "scan_enabled_at": now.isoformat() if payload.enabled else (target.config or {}).get("scan_enabled_at"),
        "scan_last_run_at": (target.config or {}).get("scan_last_run_at"),
        "scan_next_run_at": (now + timedelta(hours=payload.scan_interval_hours)).isoformat() if payload.enabled else None,
    }
    target.updated_at = now
    db.commit()
    db.refresh(target)
    return _serialize_target(target)
