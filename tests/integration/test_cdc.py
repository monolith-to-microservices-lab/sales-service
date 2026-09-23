"""Tests for the CDC consumer (app/cdc). No Kafka broker involved: Kafka
messages and the consumer client are faked in-process, and events are applied
to the same real test PostgreSQL the rest of the suite uses (see conftest.py,
which points at an isolated `sales_test` database and refuses to run
otherwise). Mirrors user-service/tests/test_cdc.py.
"""

import json

import pytest
from sqlalchemy import select

import app.cdc.consumer as consumer_module
from app.cdc.consumer import SalesCdcConsumer
from app.database import SessionLocal
from app.models import Sale


class FakeMessage:
    def __init__(self, value: bytes | None, *, topic="legacy.public.sales", partition=0, offset=0):
        self._value = value
        self._topic = topic
        self._partition = partition
        self._offset = offset

    def topic(self):
        return self._topic

    def partition(self):
        return self._partition

    def offset(self):
        return self._offset

    def value(self):
        return self._value

    def error(self):
        return None


class FakeKafkaConsumer:
    """Minimal double for confluent_kafka.Consumer: records committed offsets."""

    def __init__(self):
        self.committed_offsets: list[int] = []

    def subscribe(self, topics):
        pass

    def commit(self, message=None, asynchronous=False):
        self.committed_offsets.append(message.offset())

    def close(self):
        pass


def _sale_row(
    id: int,
    user_id: int,
    item_name: str = "Widget",
    quantity: int = 1,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "id": id,
        "user_id": user_id,
        "item_name": item_name,
        "quantity": quantity,
        "created_at": created_at,
    }


def _message(
    op: str,
    offset: int,
    *,
    before: dict | None = None,
    after: dict | None = None,
    source_ts_ms: int = 1,
) -> FakeMessage:
    body = {
        "op": op,
        "before": before,
        "after": after,
        "source": {"table": "sales", "ts_ms": source_ts_ms},
        "ts_ms": source_ts_ms,
    }
    return FakeMessage(json.dumps(body).encode("utf-8"), offset=offset)


@pytest.fixture
def db_session():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def cdc_consumer():
    fake = FakeKafkaConsumer()
    return SalesCdcConsumer(fake, session_factory=SessionLocal), fake


def _get(session, sale_id: int) -> Sale | None:
    session.expire_all()
    return session.scalar(select(Sale).where(Sale.id == sale_id))


def test_create_event_inserts_sale(cdc_consumer, db_session):
    consumer, fake = cdc_consumer
    consumer.process_message(
        _message("c", 0, after=_sale_row(701, user_id=1, item_name="Keyboard"))
    )

    sale = _get(db_session, 701)
    assert sale is not None
    assert sale.item_name == "Keyboard"
    assert sale.user_id == 1
    assert fake.committed_offsets == [0]


def test_update_event_updates_existing_sale(cdc_consumer, db_session):
    consumer, fake = cdc_consumer
    consumer.process_message(
        _message("c", 0, after=_sale_row(702, user_id=1, item_name="Mouse", quantity=1))
    )
    consumer.process_message(
        _message(
            "u",
            1,
            before=_sale_row(702, user_id=1, item_name="Mouse", quantity=1),
            after=_sale_row(702, user_id=1, item_name="Mouse", quantity=5),
        )
    )

    sale = _get(db_session, 702)
    assert sale.quantity == 5
    assert fake.committed_offsets == [0, 1]


def test_delete_event_removes_sale(cdc_consumer, db_session):
    consumer, fake = cdc_consumer
    consumer.process_message(_message("c", 0, after=_sale_row(703, user_id=1)))
    consumer.process_message(_message("d", 1, before=_sale_row(703, user_id=1)))

    assert _get(db_session, 703) is None
    assert fake.committed_offsets == [0, 1]


def test_tombstone_is_skipped_but_offset_is_committed(cdc_consumer, db_session):
    consumer, fake = cdc_consumer
    consumer.process_message(_message("c", 0, after=_sale_row(704, user_id=1)))
    tombstone = FakeMessage(None, offset=1)
    consumer.process_message(tombstone)

    assert fake.committed_offsets == [0, 1]
    # tombstone carries no operation - the row from the prior create is untouched.
    assert _get(db_session, 704) is not None


def test_reprocessing_same_message_is_idempotent(cdc_consumer, db_session):
    """Redelivery of the exact same message (e.g. consumer crashed right after
    the DB commit but before the Kafka offset commit landed) must not create a
    duplicate row.
    """
    consumer, fake = cdc_consumer
    msg = _message("c", 0, after=_sale_row(705, user_id=1, item_name="Monitor"))

    consumer.process_message(msg)
    consumer.process_message(msg)

    rows = db_session.scalars(select(Sale).where(Sale.id == 705)).all()
    assert len(rows) == 1
    assert rows[0].item_name == "Monitor"


def test_db_error_is_not_committed_and_offset_is_not_advanced(
    cdc_consumer, db_session, monkeypatch
):
    consumer, fake = cdc_consumer

    def _boom(session, envelope):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(consumer_module, "apply_sale_event", _boom)

    msg = _message("c", 0, after=_sale_row(706, user_id=1))
    with pytest.raises(RuntimeError):
        consumer.process_message(msg)

    assert fake.committed_offsets == []
    assert _get(db_session, 706) is None


def test_restart_redelivery_does_not_duplicate_sale(db_session):
    """Simulates a consumer restart: a brand new SalesCdcConsumer instance
    (fresh Kafka client, same database) reprocesses a message whose offset was
    never committed by the previous run. End state must still be one row.
    """
    msg = _message("c", 0, after=_sale_row(707, user_id=1, item_name="Chair"))

    first_run = SalesCdcConsumer(FakeKafkaConsumer(), session_factory=SessionLocal)
    first_run.process_message(msg)

    second_run = SalesCdcConsumer(FakeKafkaConsumer(), session_factory=SessionLocal)
    second_run.process_message(msg)

    rows = db_session.scalars(select(Sale).where(Sale.id == 707)).all()
    assert len(rows) == 1
    assert rows[0].item_name == "Chair"


def test_sale_referencing_user_not_locally_known_is_applied_without_validation(
    cdc_consumer, db_session
):
    """The Sales Service's Sale model has no FK to a local users table (it
    lives in a different database/service) - a sale event for a user_id this
    service has never heard of must still apply cleanly, with no synchronous
    call to user-service and no blocking/ordering dependency on the users
    topic having been processed first.
    """
    consumer, fake = cdc_consumer
    NEVER_SEEN_USER_ID = 999_999

    consumer.process_message(_message("c", 0, after=_sale_row(708, user_id=NEVER_SEEN_USER_ID)))

    sale = _get(db_session, 708)
    assert sale is not None
    assert sale.user_id == NEVER_SEEN_USER_ID
    assert fake.committed_offsets == [0]


def test_sale_events_processed_out_of_order_still_converge(cdc_consumer, db_session):
    """Documents the out-of-order case: if an update for a sale somehow gets
    processed before its create (should not happen with a single partition,
    but the topic could be repartitioned later), upsert-by-id means the
    'create' that follows does not clobber the newer state, since it is only
    ever applied as the literal 'after' payload of ITS OWN event - each event
    is idempotent and self-contained, so processing order changes which
    values win, but never causes an error or a corrupt row.
    """
    consumer, fake = cdc_consumer

    # "update" arrives first for a sale this consumer has never seen.
    consumer.process_message(
        _message(
            "u",
            0,
            before=_sale_row(709, user_id=1, item_name="Early"),
            after=_sale_row(709, user_id=1, item_name="Updated-First"),
        )
    )
    sale = _get(db_session, 709)
    assert sale is not None
    assert sale.item_name == "Updated-First"

    # the "create" for the same id arrives afterward (out of order) - upsert
    # semantics mean it just re-applies its own after-state, no error.
    consumer.process_message(
        _message("c", 1, after=_sale_row(709, user_id=1, item_name="Created-Second"))
    )
    sale = _get(db_session, 709)
    assert sale.item_name == "Created-Second"
    assert fake.committed_offsets == [0, 1]
