"""Unit tests for the Debezium event parser (app/cdc/events.py) in isolation.
Mirrors user-service/tests/unit/test_events.py; field names adapted to Sale.

Scope boundary: events.py does STRUCTURAL parsing only. Semantic validation
(e.g. "create needs an after payload") is apply.py's job - see test_apply.py.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.cdc.events import DebeziumSaleEnvelope, SaleCdcPayload

VALID_AFTER = {
    "id": 501,
    "user_id": 1,
    "item_name": "Widget",
    "quantity": 2,
    "created_at": "2026-01-01T00:00:00Z",
}


class TestSuccessCases:
    def test_create_event(self):
        envelope = DebeziumSaleEnvelope.model_validate(
            {"before": None, "after": VALID_AFTER, "op": "c"}
        )
        assert envelope.op == "c"
        assert envelope.after.id == 501
        assert envelope.after.user_id == 1

    def test_read_snapshot_event(self):
        envelope = DebeziumSaleEnvelope.model_validate(
            {"before": None, "after": VALID_AFTER, "op": "r"}
        )
        assert envelope.op == "r"

    def test_update_event_has_both_before_and_after(self):
        before = {**VALID_AFTER, "quantity": 1}
        envelope = DebeziumSaleEnvelope.model_validate(
            {"before": before, "after": VALID_AFTER, "op": "u"}
        )
        assert envelope.before.quantity == 1
        assert envelope.after.quantity == 2

    def test_delete_event_uses_before_after_is_none(self):
        envelope = DebeziumSaleEnvelope.model_validate(
            {"before": VALID_AFTER, "after": None, "op": "d"}
        )
        assert envelope.before.id == 501
        assert envelope.after is None

    def test_source_ts_ms_is_captured_for_latency(self):
        envelope = DebeziumSaleEnvelope.model_validate(
            {
                "after": VALID_AFTER,
                "op": "c",
                "source": {"ts_ms": 1700000000123},
                "ts_ms": 1700000000456,
            }
        )
        assert envelope.source.ts_ms == 1700000000123

    def test_unknown_extra_fields_are_ignored(self):
        envelope = DebeziumSaleEnvelope.model_validate(
            {
                "before": None,
                "after": VALID_AFTER,
                "op": "c",
                "transaction": None,
                "source": {"ts_ms": 1, "lsn": 123456, "txId": 42, "table": "sales"},
            }
        )
        assert envelope.op == "c"

    def test_user_id_referencing_unknown_user_still_parses(self):
        """No FK validation happens at parse time (or ever, locally) - see
        the "Referential Integrity" design note in the README.
        """
        envelope = DebeziumSaleEnvelope.model_validate(
            {"after": {**VALID_AFTER, "user_id": 999_999}, "op": "c"}
        )
        assert envelope.after.user_id == 999_999


class TestFailureCases:
    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            json.loads(b"{not valid json")

    def test_missing_op_raises(self):
        with pytest.raises(ValidationError):
            DebeziumSaleEnvelope.model_validate({"before": None, "after": VALID_AFTER})

    def test_op_is_required_to_be_a_string(self):
        with pytest.raises(ValidationError):
            DebeziumSaleEnvelope.model_validate({"after": VALID_AFTER, "op": 123})

    def test_unknown_op_value_parses_but_is_rejected_later_by_apply(self):
        envelope = DebeziumSaleEnvelope.model_validate({"after": VALID_AFTER, "op": "x"})
        assert envelope.op == "x"

    def test_payload_missing_id_raises(self):
        with pytest.raises(ValidationError):
            SaleCdcPayload.model_validate(
                {
                    "user_id": 1,
                    "item_name": "Widget",
                    "quantity": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )

    def test_payload_missing_user_id_raises(self):
        with pytest.raises(ValidationError):
            SaleCdcPayload.model_validate(
                {
                    "id": 1,
                    "item_name": "Widget",
                    "quantity": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )

    def test_payload_id_wrong_type_raises(self):
        with pytest.raises(ValidationError):
            SaleCdcPayload.model_validate(
                {
                    "id": "not-a-number",
                    "user_id": 1,
                    "item_name": "W",
                    "quantity": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )

    def test_payload_quantity_wrong_type_raises(self):
        with pytest.raises(ValidationError):
            SaleCdcPayload.model_validate(
                {
                    "id": 1,
                    "user_id": 1,
                    "item_name": "W",
                    "quantity": "many",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )

    def test_payload_invalid_timestamp_raises(self):
        with pytest.raises(ValidationError):
            SaleCdcPayload.model_validate(
                {
                    "id": 1,
                    "user_id": 1,
                    "item_name": "W",
                    "quantity": 1,
                    "created_at": "not-a-timestamp",
                }
            )

    def test_empty_dict_raises(self):
        with pytest.raises(ValidationError):
            DebeziumSaleEnvelope.model_validate({})
