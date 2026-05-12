from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.deps import get_current_user
from backend.app.core.time import shanghai_now
from backend.app.models import KeywordGroup, Note, User
from backend.app.schemas.common import paginated_query
from backend.app.services.note_util import note_haystack, note_metrics

router = APIRouter(prefix="/keyword-groups", tags=["keyword-groups"])


class KeywordGroupCreateRequest(BaseModel):
    platform: Literal["xhs", "douyin", "kuaishou", "weibo", "xianyu", "taobao"] = "xhs"
    name: str = Field(min_length=1, max_length=128)
    keywords: list[str] = Field(min_length=1, max_length=50)


class KeywordGroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    keywords: list[str] | None = Field(default=None, min_length=1, max_length=50)


def _normalize_keywords(keywords: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = keyword.strip()
        key = value.lower()
        if value and key not in seen:
            normalized.append(value)
            seen.add(key)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one keyword is required")
    return normalized


def _serialize_group(group: KeywordGroup) -> dict[str, Any]:
    return {
        "id": group.id,
        "platform": group.platform,
        "name": group.name,
        "keywords": group.keywords or [],
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat(),
    }


def _get_owned_group(db: Session, current_user: User, group_id: int) -> KeywordGroup:
    group = db.get(KeywordGroup, group_id)
    if group is None or group.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Keyword group not found")
    return group


def _owned_notes(db: Session, current_user: User, platform: str) -> list[Note]:
    return db.scalars(
        select(Note)
        .where(Note.user_id == current_user.id, Note.platform == platform)
        .order_by(Note.created_at.desc(), Note.id.desc())
    ).all()


def _trend_summary(db: Session, current_user: User, group: KeywordGroup) -> dict[str, Any]:
    notes = _owned_notes(db, current_user, group.platform)
    keyword_items: list[dict[str, Any]] = []
    matched_by_note_id: dict[int, dict[str, Any]] = {}
    for keyword in group.keywords or []:
        needle = keyword.lower()
        matched_notes = [note for note in notes if needle in note_haystack(note)]
        engagement = sum(note_metrics(note)["engagement"] for note in matched_notes)
        keyword_items.append({"keyword": keyword, "notes": len(matched_notes), "engagement": engagement})
        for note in matched_notes:
            metrics = note_metrics(note)
            matched_by_note_id[note.id] = {
                "id": note.id,
                "note_id": note.note_id,
                "title": note.title,
                "author_name": note.author_name,
                "created_at": note.created_at.isoformat(),
                **metrics,
            }
    matched_notes = sorted(matched_by_note_id.values(), key=lambda item: item["engagement"], reverse=True)
    return {
        "total_matches": len(matched_notes),
        "total_engagement": sum(item["engagement"] for item in matched_notes),
        "keywords": keyword_items,
        "matched_notes": matched_notes[:10],
    }


@router.get("")
def list_keyword_groups(
    platform: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statement = select(KeywordGroup).where(KeywordGroup.user_id == current_user.id)
    if platform:
        statement = statement.where(KeywordGroup.platform == platform)
    statement = statement.order_by(KeywordGroup.created_at.desc(), KeywordGroup.id.desc())
    return paginated_query(db, statement, page=page, page_size=page_size, map_item=_serialize_group)


@router.post("")
def create_keyword_group(
    payload: KeywordGroupCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = KeywordGroup(
        user_id=current_user.id,
        platform=payload.platform,
        name=payload.name.strip(),
        keywords=_normalize_keywords(payload.keywords),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return _serialize_group(group)


@router.get("/{group_id}")
def get_keyword_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = _get_owned_group(db, current_user, group_id)
    serialized = _serialize_group(group)
    serialized["trend"] = _trend_summary(db, current_user, group)
    return serialized


@router.patch("/{group_id}")
def update_keyword_group(
    group_id: int,
    payload: KeywordGroupUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = _get_owned_group(db, current_user, group_id)
    if payload.name is not None:
        group.name = payload.name.strip()
    if payload.keywords is not None:
        group.keywords = _normalize_keywords(payload.keywords)
    group.updated_at = shanghai_now()
    db.commit()
    db.refresh(group)
    return _serialize_group(group)


@router.delete("/{group_id}")
def delete_keyword_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = _get_owned_group(db, current_user, group_id)
    db.delete(group)
    db.commit()
    return {"id": group_id, "status": "deleted"}
