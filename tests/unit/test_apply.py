"""Unit tests for app/cdc/apply.py in isolation: pure upsert/delete logic
against an in-memory SQLite session (no Postgres, no Kafka). Mirrors
user-service/tests/unit/test_apply.py; field names adapted to Sale.
"""
from __future__ import annotations

import pytest

from app.cdc.apply import apply_sale_event
from app.cdc.events import DebeziumSaleEnvelope
from app.models import Sale
from tests.unit.conftest import get_sale

AFTER = {"id": 501, "user_id": 1, "item_name": "Widget", "quantity": 2, "created_at": "2026-01-01T00:00:00Z"}


def _envelope(**kw) -> DebeziumSaleEnvelope:
    return DebeziumSaleEnvelope.model_validate(kw)


class TestCreate:
    def test_create_inserts_new_sale(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        sale = get_sale(db_session, 501)
        assert sale is not None
        assert sale.item_name == "Widget"
        assert sale.quantity == 2

    def test_create_missing_after_raises(self, db_session):
        with pytest.raises(ValueError, match="after"):
            apply_sale_event(db_session, _envelope(after=None, op="c"))
        assert get_sale(db_session, 501) is None

    def test_read_snapshot_behaves_like_create(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="r"))
        assert get_sale(db_session, 501) is not None


class TestDuplicateCreate:
    def test_duplicate_create_upserts_instead_of_duplicating(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        apply_sale_event(db_session, _envelope(after={**AFTER, "quantity": 9}, op="c"))

        rows = db_session.query(Sale).filter(Sale.id == 501).all()
        assert len(rows) == 1
        assert rows[0].quantity == 9


class TestUpdate:
    def test_update_existing_sale_changes_fields(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        apply_sale_event(
            db_session, _envelope(before=AFTER, after={**AFTER, "quantity": 10, "item_name": "Gadget"}, op="u")
        )
        sale = get_sale(db_session, 501)
        assert sale.quantity == 10
        assert sale.item_name == "Gadget"

    def test_update_without_existing_row_upserts(self, db_session):
        apply_sale_event(db_session, _envelope(before=None, after=AFTER, op="u"))
        assert get_sale(db_session, 501) is not None

    def test_update_missing_after_raises(self, db_session):
        with pytest.raises(ValueError, match="after"):
            apply_sale_event(db_session, _envelope(before=AFTER, after=None, op="u"))

    def test_update_changes_user_id(self, db_session):
        """Reassigning a sale to a different user is a plain field update -
        no FK to validate against locally (see README "Referential Integrity").
        """
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        apply_sale_event(db_session, _envelope(before=AFTER, after={**AFTER, "user_id": 42}, op="u"))
        assert get_sale(db_session, 501).user_id == 42


class TestDelete:
    def test_delete_existing_sale_removes_it(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        apply_sale_event(db_session, _envelope(before=AFTER, op="d"))
        assert get_sale(db_session, 501) is None

    def test_delete_nonexistent_sale_is_a_safe_no_op(self, db_session):
        apply_sale_event(db_session, _envelope(before=AFTER, op="d"))
        assert get_sale(db_session, 501) is None

    def test_delete_missing_before_is_a_safe_no_op(self, db_session):
        apply_sale_event(db_session, _envelope(before=None, op="d"))


class TestUnknownOperation:
    def test_unknown_op_raises_valueerror(self, db_session):
        with pytest.raises(ValueError, match="unsupported CDC op"):
            apply_sale_event(db_session, _envelope(after=AFTER, op="x"))
        assert get_sale(db_session, 501) is None


class TestUserReference:
    def test_sale_with_never_seen_user_id_is_applied_without_validation(self, db_session):
        apply_sale_event(db_session, _envelope(after={**AFTER, "user_id": 999_999}, op="c"))
        sale = get_sale(db_session, 501)
        assert sale is not None
        assert sale.user_id == 999_999


class TestIdempotency:
    def test_same_create_applied_twice_is_idempotent(self, db_session):
        env = _envelope(after=AFTER, op="c")
        apply_sale_event(db_session, env)
        apply_sale_event(db_session, env)
        assert db_session.query(Sale).filter(Sale.id == 501).count() == 1

    def test_same_update_applied_twice_is_idempotent(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        env = _envelope(before=AFTER, after={**AFTER, "quantity": 7}, op="u")
        apply_sale_event(db_session, env)
        apply_sale_event(db_session, env)
        assert get_sale(db_session, 501).quantity == 7

    def test_same_delete_applied_twice_is_idempotent(self, db_session):
        apply_sale_event(db_session, _envelope(after=AFTER, op="c"))
        env = _envelope(before=AFTER, op="d")
        apply_sale_event(db_session, env)
        apply_sale_event(db_session, env)
        assert get_sale(db_session, 501) is None
