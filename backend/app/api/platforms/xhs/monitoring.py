from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.platforms.xhs.pc import get_xhs_pc_api_adapter_factory
from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.models import MonitoringSnapshot, MonitoringTarget, Note, PlatformAccount, User
from backend.app.schemas.common import paginated_query
from backend.app.services.monitoring_crawl_service import execute_monitoring_refresh
from backend.app.services.note_util import note_matches_target, note_metrics

router = APIRouter(prefix="/xhs/monitoring", tags=["xhs-monitoring"])


class MonitoringTargetCreateRequest(BaseModel):
    target_type: Literal["keyword", "account", "brand", "note_url"]
    name: str = Field(default="", max_length=512)
    value: str = Field(min_length=1, max_length=512)
    status: Literal["active", "paused"] = "active"
    config: dict[str, Any] = Field(default_factory=dict)
    platform_account_id: int | None = None


class MonitoringTargetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=512)
    value: str | None = Field(default=None, min_length=1, max_length=512)
    status: Literal["active", "paused"] | None = None
    config: dict[str, Any] | None = None


def _serialize_target(target: MonitoringTarget) -> dict[str, Any]:
    return {
        "id": target.id,
        "platform": target.platform,
        "target_type": target.target_type,
        "name": target.name,
        "value": target.value,
        "status": target.status,
        "config": target.config or {},
        "last_refreshed_at": target.last_refreshed_at.isoformat() if target.last_refreshed_at else None,
        "created_at": target.created_at.isoformat(),
        "updated_at": target.updated_at.isoformat(),
        "platform_account_id": target.platform_account_id,
        "crawl_interval_minutes": target.crawl_interval_minutes,
        "consecutive_failures": target.consecutive_failures,
        "last_crawl_error": target.last_crawl_error,
    }


def _serialize_snapshot(snapshot: MonitoringSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "target_id": snapshot.target_id,
        "payload": snapshot.payload or {},
        "created_at": snapshot.created_at.isoformat(),
    }


def _serialize_monitoring_note(note: Note) -> dict[str, Any]:
    return {
        "id": note.id,
        "note_id": note.note_id,
        "title": note.title,
        "author_name": note.author_name,
        "created_at": note.created_at.isoformat(),
        **note_metrics(note),
    }


def _matching_notes(db: Session, current_user: User, target: MonitoringTarget) -> list[Note]:
    notes = db.scalars(
        select(Note)
        .where(
            Note.user_id == current_user.id,
            Note.platform == "xhs",
        )
        .order_by(Note.created_at.desc(), Note.id.desc())
    ).all()
    matched = [note for note in notes if note_matches_target(note, target)]
    return sorted(matched, key=lambda note: note_metrics(note)["engagement"], reverse=True)


def _get_owned_target(db: Session, current_user: User, target_id: int) -> MonitoringTarget:
    target = db.get(MonitoringTarget, target_id)
    if target is None or target.user_id != current_user.id or target.platform != "xhs":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitoring target not found")
    return target


@router.get("/targets")
def targets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = (
        select(MonitoringTarget)
        .where(MonitoringTarget.user_id == current_user.id, MonitoringTarget.platform == "xhs")
        .order_by(MonitoringTarget.created_at.desc(), MonitoringTarget.id.desc())
    )
    return paginated_query(db, statement, page=page, page_size=page_size, map_item=_serialize_target)


@router.post("/targets")
def create_target(
    payload: MonitoringTargetCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    platform_account_id: int | None = None
    if payload.platform_account_id is not None:
        account = db.get(PlatformAccount, payload.platform_account_id)
        if account is not None and account.user_id == current_user.id and account.platform == "xhs" and account.sub_type == "pc":
            platform_account_id = account.id

    target = MonitoringTarget(
        user_id=current_user.id,
        platform="xhs",
        target_type=payload.target_type,
        name=payload.name or payload.value,
        value=payload.value,
        status=payload.status,
        config=payload.config,
        platform_account_id=platform_account_id,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return _serialize_target(target)


@router.patch("/targets/{target_id}")
def update_target(
    target_id: int,
    payload: MonitoringTargetUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_target(db, current_user, target_id)
    if payload.name is not None:
        target.name = payload.name
    if payload.value is not None:
        target.value = payload.value
    if payload.status is not None:
        target.status = payload.status
    if payload.config is not None:
        target.config = payload.config
    target.updated_at = shanghai_now()
    db.commit()
    db.refresh(target)
    return _serialize_target(target)


@router.delete("/targets/{target_id}")
def delete_target(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_target(db, current_user, target_id)
    db.delete(target)
    db.commit()
    return {"id": target_id, "status": "deleted"}


@router.post("/targets/{target_id}/refresh")
def refresh_target(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    adapter_factory=Depends(get_xhs_pc_api_adapter_factory),
):
    target = _get_owned_target(db, current_user, target_id)
    return execute_monitoring_refresh(db, target, current_user, adapter_factory=adapter_factory)


@router.get("/targets/{target_id}/notes")
def target_notes(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_target(db, current_user, target_id)
    return {"target_id": target.id, "items": [_serialize_monitoring_note(note) for note in _matching_notes(db, current_user, target)]}


@router.get("/targets/{target_id}/snapshots")
def target_snapshots(
    target_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target = _get_owned_target(db, current_user, target_id)
    snapshots = db.scalars(
        select(MonitoringSnapshot)
        .where(MonitoringSnapshot.target_id == target.id)
        .order_by(MonitoringSnapshot.created_at.desc(), MonitoringSnapshot.id.desc())
    ).all()
    return {"target_id": target.id, "items": [_serialize_snapshot(snapshot) for snapshot in snapshots]}
