"""Integration tests against the REAL Kafka broker from cdc-infrastructure
(localhost:9092) and the real `sales_test` Postgres. Mirrors
user-service/tests/integration/test_kafka_real.py; adapted to Sale fields.
Every test uses its own uniquely-named topic and consumer group.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from confluent_kafka import Consumer, Producer
from sqlalchemy import select

from app.cdc.consumer import SalesCdcConsumer
from app.database import SessionLocal
from app.models import Sale

pytestmark = pytest.mark.slow

KAFKA_BOOTSTRAP = "localhost:9092"


def _unique_topic() -> str:
    return f"test.legacy.public.sales.{uuid.uuid4().hex[:8]}"


def _unique_group() -> str:
    return f"test-sales-cdc-{uuid.uuid4().hex[:8]}"


def _envelope_bytes(op, before=None, after=None, source_ts_ms=None) -> bytes | None:
    if op is None:
        return None
    body = {
        "before": before,
        "after": after,
        "op": op,
        "source": {"ts_ms": source_ts_ms or int(time.time() * 1000)},
        "ts_ms": int(time.time() * 1000),
    }
    return json.dumps(body).encode("utf-8")


@pytest.fixture
def producer():
    p = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})
    yield p
    p.flush(10)


def _produce(producer, topic, entity_id, op, **kw):
    producer.produce(topic, key=str(entity_id).encode(), value=_envelope_bytes(op, **kw))
    producer.flush(10)


def _make_consumer(topic: str, group: str, session_factory=SessionLocal):
    raw = Consumer(
        {"bootstrap.servers": KAFKA_BOOTSTRAP, "group.id": group,
         "auto.offset.reset": "earliest", "enable.auto.commit": False}
    )
    wrapper = SalesCdcConsumer(raw, session_factory=session_factory, topic=topic)
    wrapper.subscribe()
    return wrapper, raw


def _drain_one(wrapper: SalesCdcConsumer, raw, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = raw.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            continue
        wrapper.process_message(msg)
        return msg
    raise TimeoutError(f"no message arrived within {timeout}s on {wrapper._topic}")


def _get_sale(session, sale_id):
    session.expire_all()
    return session.get(Sale, sale_id)


def _payload(id_, user_id=1, item_name="Real Kafka Sale", quantity=1, created_at="2026-01-01T00:00:00Z"):
    return {"id": id_, "user_id": user_id, "item_name": item_name, "quantity": quantity, "created_at": created_at}


class TestRealKafkaCreateUpdateDeleteTombstone:
    def test_full_cycle_through_a_real_broker(self, producer, db_session):
        topic, group = _unique_topic(), _unique_group()
        wrapper, raw = _make_consumer(topic, group)
        entity_id = 80001
        try:
            _produce(producer, topic, entity_id, "c", after=_payload(entity_id, item_name="Created"))
            _drain_one(wrapper, raw)
            assert _get_sale(db_session, entity_id).item_name == "Created"

            _produce(producer, topic, entity_id, "u", before=_payload(entity_id, item_name="Created"),
                      after=_payload(entity_id, item_name="Updated", quantity=5))
            _drain_one(wrapper, raw)
            sale = _get_sale(db_session, entity_id)
            assert sale.item_name == "Updated" and sale.quantity == 5

            _produce(producer, topic, entity_id, "d", before=_payload(entity_id, item_name="Updated"))
            _drain_one(wrapper, raw)
            assert _get_sale(db_session, entity_id) is None

            producer.produce(topic, key=str(entity_id).encode(), value=None)
            producer.flush(10)
            _drain_one(wrapper, raw)  # tombstone: must not raise, no DB effect
            assert _get_sale(db_session, entity_id) is None
        finally:
            raw.close()


class TestDuplicateDelivery:
    def test_same_message_processed_twice_does_not_duplicate(self, producer, db_session):
        topic, group = _unique_topic(), _unique_group()
        wrapper, raw = _make_consumer(topic, group)
        entity_id = 80002
        try:
            _produce(producer, topic, entity_id, "c", after=_payload(entity_id))
            msg = _drain_one(wrapper, raw)
            wrapper.process_message(msg)  # replay

            rows = db_session.scalars(select(Sale).where(Sale.id == entity_id)).all()
            assert len(rows) == 1
        finally:
            raw.close()


class TestConsumerRestart:
    def test_new_consumer_instance_same_group_continues_from_committed_offset(self, producer, db_session):
        topic, group = _unique_topic(), _unique_group()
        wrapper1, raw1 = _make_consumer(topic, group)
        e1, e2 = 80003, 80004
        try:
            _produce(producer, topic, e1, "c", after=_payload(e1, item_name="First"))
            _drain_one(wrapper1, raw1)
        finally:
            raw1.close()

        wrapper2, raw2 = _make_consumer(topic, group)
        try:
            _produce(producer, topic, e2, "c", after=_payload(e2, item_name="Second"))
            _drain_one(wrapper2, raw2)
            assert _get_sale(db_session, e1).item_name == "First"
            assert _get_sale(db_session, e2).item_name == "Second"
        finally:
            raw2.close()


class TestCrashBeforeDbCommit:
    def test_redelivery_after_apply_failure_ends_in_exactly_one_correct_row(self, producer, db_session, monkeypatch):
        topic, group = _unique_topic(), _unique_group()
        entity_id = 80005

        import app.cdc.consumer as consumer_module

        real_apply = consumer_module.apply_sale_event
        monkeypatch.setattr(
            consumer_module, "apply_sale_event",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated crash before db commit")),
        )
        wrapper1, raw1 = _make_consumer(topic, group)
        try:
            _produce(producer, topic, entity_id, "c", after=_payload(entity_id, item_name="Crashed"))
            with pytest.raises(RuntimeError, match="simulated crash"):
                _drain_one(wrapper1, raw1)
            assert _get_sale(db_session, entity_id) is None
        finally:
            raw1.close()

        monkeypatch.setattr(consumer_module, "apply_sale_event", real_apply)
        wrapper2, raw2 = _make_consumer(topic, group)
        try:
            _drain_one(wrapper2, raw2)
            assert _get_sale(db_session, entity_id).item_name == "Crashed"
        finally:
            raw2.close()


class _FlakyCommitConsumer:
    def __init__(self, real_consumer, fail_times: int = 1):
        self._real = real_consumer
        self._fail_times = fail_times

    def subscribe(self, *a, **kw):
        return self._real.subscribe(*a, **kw)

    def poll(self, *a, **kw):
        return self._real.poll(*a, **kw)

    def commit(self, *a, **kw):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("simulated broker unavailable at offset-commit time")
        return self._real.commit(*a, **kw)

    def close(self):
        return self._real.close()


class TestCrashAfterDbCommitBeforeOffsetCommit:
    def test_offset_not_committed_then_redelivered_converges_correctly(self, producer, db_session):
        topic, group = _unique_topic(), _unique_group()
        entity_id = 80006

        real_raw1 = Consumer(
            {"bootstrap.servers": KAFKA_BOOTSTRAP, "group.id": group,
             "auto.offset.reset": "earliest", "enable.auto.commit": False}
        )
        flaky1 = _FlakyCommitConsumer(real_raw1, fail_times=1)
        wrapper1 = SalesCdcConsumer(flaky1, session_factory=SessionLocal, topic=topic)
        wrapper1.subscribe()
        try:
            _produce(producer, topic, entity_id, "c", after=_payload(entity_id, item_name="Committed"))
            with pytest.raises(RuntimeError, match="simulated broker unavailable"):
                _drain_one(wrapper1, flaky1)
            assert _get_sale(db_session, entity_id).item_name == "Committed"
        finally:
            real_raw1.close()

        wrapper2, raw2 = _make_consumer(topic, group)
        try:
            _drain_one(wrapper2, raw2)
            rows = db_session.scalars(select(Sale).where(Sale.id == entity_id)).all()
            assert len(rows) == 1
            assert rows[0].item_name == "Committed"
        finally:
            raw2.close()


class TestOutOfOrder:
    def test_update_before_create_still_converges(self, producer, db_session):
        topic, group = _unique_topic(), _unique_group()
        wrapper, raw = _make_consumer(topic, group)
        entity_id = 80007
        try:
            _produce(producer, topic, entity_id, "u", before=_payload(entity_id, item_name="Never-existed"),
                      after=_payload(entity_id, item_name="Updated-First"))
            _drain_one(wrapper, raw)
            assert _get_sale(db_session, entity_id).item_name == "Updated-First"

            _produce(producer, topic, entity_id, "c", after=_payload(entity_id, item_name="Created-Second"))
            _drain_one(wrapper, raw)
            assert _get_sale(db_session, entity_id).item_name == "Created-Second"
        finally:
            raw.close()


class TestUserReference:
    def test_sale_with_never_seen_user_id_via_real_kafka(self, producer, db_session):
        topic, group = _unique_topic(), _unique_group()
        wrapper, raw = _make_consumer(topic, group)
        entity_id = 80008
        try:
            _produce(producer, topic, entity_id, "c", after=_payload(entity_id, user_id=999_999))
            _drain_one(wrapper, raw)
            sale = _get_sale(db_session, entity_id)
            assert sale is not None
            assert sale.user_id == 999_999
        finally:
            raw.close()


class TestDestinationDatabaseDown:
    def test_broken_session_factory_fails_without_advancing_offset(self, producer):
        topic, group = _unique_topic(), _unique_group()

        def _broken_session_factory():
            raise RuntimeError("simulated destination database down")

        raw = Consumer(
            {"bootstrap.servers": KAFKA_BOOTSTRAP, "group.id": group,
             "auto.offset.reset": "earliest", "enable.auto.commit": False}
        )
        wrapper = SalesCdcConsumer(raw, session_factory=_broken_session_factory, topic=topic)
        wrapper.subscribe()
        try:
            _produce(producer, topic, 80009, "c", after=_payload(80009))
            with pytest.raises(RuntimeError, match="database down"):
                _drain_one(wrapper, raw)
        finally:
            raw.close()


class TestObservabilityAfterRealPublish:
    def test_metrics_increment_after_a_real_produce_consume_cycle(self, producer, db_session):
        from app.cdc import metrics

        topic, group = _unique_topic(), _unique_group()
        wrapper, raw = _make_consumer(topic, group)
        entity_id = 80010
        before = metrics.EVENTS_PROCESSED_TOTAL.labels(service=metrics.SERVICE_NAME, operation="c")._value.get()
        try:
            _produce(producer, topic, entity_id, "c", after=_payload(entity_id))
            _drain_one(wrapper, raw)
        finally:
            raw.close()

        after = metrics.EVENTS_PROCESSED_TOTAL.labels(service=metrics.SERVICE_NAME, operation="c")._value.get()
        assert after == before + 1
