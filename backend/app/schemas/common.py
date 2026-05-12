from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def paginated(items: Iterable[Any], page: int = 1, page_size: int = 20) -> dict:
    """Paginate after fetching (fallback for non-SQLAlchemy data)."""
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), 100)
    materialized = list(items)
    start = (safe_page - 1) * safe_page_size
    end = start + safe_page_size
    return {
        "total": len(materialized),
        "page": safe_page,
        "page_size": safe_page_size,
        "items": materialized[start:end],
    }


def paginated_query(
    db: Session,
    statement: Any,
    *,
    page: int = 1,
    page_size: int = 20,
    map_item: Any = None,
) -> dict:
    """Paginate at the database layer using offset/limit.

    Args:
        db: SQLAlchemy session.
        statement: A select() statement (may include .order_by(), .where(), etc.).
        page: 1-based page number.
        page_size: Items per page (clamped 1-100).
        map_item: Optional callable to transform each row. If None, values are
            returned as-is (assumes scalars() or scalar-style results).

    Returns:
        A dict with total, page, page_size, and items.
    """
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), 100)

    total = db.scalar(
        select(func.count()).select_from(statement.order_by().subquery())
    )

    offset = (safe_page - 1) * safe_page_size
    rows = db.scalars(statement.offset(offset).limit(safe_page_size)).all()

    items = [map_item(row) for row in rows] if map_item else list(rows)

    return {
        "total": total or 0,
        "page": safe_page,
        "page_size": safe_page_size,
        "items": items,
    }
