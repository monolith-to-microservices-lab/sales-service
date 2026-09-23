"""Apply one Debezium `sales` change event to this service's own database.

Distinct from `service.import_sale` (which is for migration-tool's one-time
snapshot and raises `ImportConflictError` on differing data - the right
behavior for a one-shot import, but wrong for CDC: an `update` event MUST
overwrite the destination row, not conflict with it). CDC apply is a plain
idempotent upsert-by-id for create/update, delete-if-present for delete -
same shape as user-service's app/cdc/apply.py.
"""

from sqlalchemy.orm import Session

from ..models import Sale
from ..service import _sync_identity_sequence
from .events import DebeziumSaleEnvelope, SaleCdcPayload

_UPSERT_OPS = {"c", "r", "u"}


def apply_sale_event(session: Session, envelope: DebeziumSaleEnvelope) -> None:
    """Idempotent: applying the same event any number of times leaves the same
    end state (upsert-by-id for create/update, delete-if-present for delete).
    Commits on success; raises and leaves nothing committed on failure.
    """
    if envelope.op in _UPSERT_OPS:
        _upsert(session, envelope.after)
    elif envelope.op == "d":
        _delete(session, envelope.before)
    else:
        raise ValueError(f"unsupported CDC op: {envelope.op!r}")
    session.commit()


def _upsert(session: Session, payload: SaleCdcPayload | None) -> None:
    if payload is None:
        raise ValueError("upsert event is missing its 'after' payload")
    sale = session.get(Sale, payload.id)
    if sale is None:
        session.add(
            Sale(
                id=payload.id,
                user_id=payload.user_id,
                item_name=payload.item_name,
                quantity=payload.quantity,
                created_at=payload.created_at,
            )
        )
        session.flush()
        _sync_identity_sequence(session)
    else:
        sale.user_id = payload.user_id
        sale.item_name = payload.item_name
        sale.quantity = payload.quantity
        sale.created_at = payload.created_at


def _delete(session: Session, payload: SaleCdcPayload | None) -> None:
    if payload is None:
        return
    sale = session.get(Sale, payload.id)
    if sale is not None:
        session.delete(sale)
