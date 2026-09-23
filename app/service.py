"""Business logic: route -> service -> SQLAlchemy -> Sales PostgreSQL.

No repository layer on purpose — the service is small.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .exceptions import ImportConflictError, NotFoundError
from .models import Sale
from .schemas import SaleCreate, SaleImport, SaleUpdate


def _normalize_dt(value: datetime) -> datetime:
    """Compare timestamps as the same instant, tolerating naive input (UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sync_identity_sequence(db: Session) -> None:
    """Keep the PostgreSQL IDENTITY sequence ahead of any manually inserted id.

    After an explicit-id insert the sequence backing `sales.id` is untouched,
    so a later `POST /sales` could try to reuse an id and collide. We realign
    the sequence to MAX(id) (is_called=true => next value is MAX(id)+1).
    """
    db.execute(
        text(
            "SELECT setval("
            "  pg_get_serial_sequence('sales', 'id'),"
            "  (SELECT MAX(id) FROM sales)"
            ")"
        )
    )


# --------------------------------------------------------------------------- #
# Normal CRUD
# --------------------------------------------------------------------------- #

def create_sale(db: Session, data: SaleCreate) -> Sale:
    sale = Sale(
        user_id=data.user_id,
        item_name=data.item_name,
        quantity=data.quantity,
    )
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return sale


def get_sale(db: Session, sale_id: int) -> Sale:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise NotFoundError(sale_id)
    return sale


def list_sales(db: Session, limit: int = 100, offset: int = 0) -> list[Sale]:
    stmt = select(Sale).order_by(Sale.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def update_sale(db: Session, sale_id: int, data: SaleUpdate) -> Sale:
    sale = get_sale(db, sale_id)
    sale.user_id = data.user_id
    sale.item_name = data.item_name
    sale.quantity = data.quantity
    db.commit()
    db.refresh(sale)
    return sale


def delete_sale(db: Session, sale_id: int) -> None:
    sale = get_sale(db, sale_id)
    db.delete(sale)
    db.commit()


# --------------------------------------------------------------------------- #
# Legacy import (idempotent)
# --------------------------------------------------------------------------- #

def import_sale(db: Session, data: SaleImport) -> tuple[Sale, str]:
    """Idempotent upsert-by-identity for legacy migration.

    - id absent            -> insert, realign sequence, return (sale, "created")
    - id present, same data -> no-op,  return (sale, "unchanged")
    - id present, diff data -> raise ImportConflictError (HTTP 409)
    """
    existing = db.get(Sale, data.id)

    if existing is None:
        sale = Sale(
            id=data.id,
            user_id=data.user_id,
            item_name=data.item_name,
            quantity=data.quantity,
            created_at=_normalize_dt(data.created_at),
        )
        db.add(sale)
        db.flush()
        _sync_identity_sequence(db)
        db.commit()
        db.refresh(sale)
        return sale, "created"

    incoming = {
        "user_id": data.user_id,
        "item_name": data.item_name,
        "quantity": data.quantity,
        "created_at": _normalize_dt(data.created_at),
    }
    current = {
        "user_id": existing.user_id,
        "item_name": existing.item_name,
        "quantity": existing.quantity,
        "created_at": _normalize_dt(existing.created_at),
    }

    if incoming == current:
        return existing, "unchanged"

    raise ImportConflictError(
        sale_id=data.id,
        current={**current, "created_at": current["created_at"].isoformat()},
        incoming={**incoming, "created_at": incoming["created_at"].isoformat()},
    )
