"""Tests for CDC consumer metric instrumentation: counters, error counters,
and the end-to-end latency calculation. Mirrors
user-service/tests/test_cdc_metrics.py. Uses the same in-process fakes as
test_cdc.py - no real Kafka broker, no dependency on Prometheus/Grafana.
"""

import json
import time

import pytest

import app.cdc.consumer as consumer_module
from app.cdc import metrics
from app.cdc.consumer import SalesCdcConsumer
from app.database import SessionLocal
from tests.integration.test_cdc import FakeKafkaConsumer, FakeMessage, _sale_row


def _message_with_source(op, offset, *, before=None, after=None, source_ts_ms=None):
    body = {
        "op": op,
        "before": before,
        "after": after,
        "source": {"table": "sales", "ts_ms": source_ts_ms} if source_ts_ms else {"table": "sales"},
        "ts_ms": source_ts_ms or 1,
    }
    return FakeMessage(json.dumps(body).encode("utf-8"), offset=offset)


def _counter_value(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


def _histogram_count(histogram, **labels) -> tuple[float, float]:
    """Reads (sum, count) via the public `.collect()` API rather than private
    attributes, so this doesn't depend on prometheus_client's internal
    Histogram field names.
    """
    for metric in histogram.collect():
        total_sum = total_count = None
        for sample in metric.samples:
            if not all(sample.labels.get(k) == v for k, v in labels.items()):
                continue
            if sample.name.endswith("_sum"):
                total_sum = sample.value
            elif sample.name.endswith("_count"):
                total_count = sample.value
        if total_sum is not None and total_count is not None:
            return total_sum, total_count
    return 0.0, 0.0


@pytest.fixture
def cdc_consumer():
    fake = FakeKafkaConsumer()
    return SalesCdcConsumer(fake, session_factory=SessionLocal), fake


def test_processed_counter_increments_per_operation(cdc_consumer):
    consumer, _ = cdc_consumer
    before = _counter_value(metrics.EVENTS_PROCESSED_TOTAL, service=metrics.SERVICE_NAME, operation="c")

    consumer.process_message(_message_with_source("c", 900, after=_sale_row(9001, user_id=1)))

    after = _counter_value(metrics.EVENTS_PROCESSED_TOTAL, service=metrics.SERVICE_NAME, operation="c")
    assert after == before + 1


def test_received_counter_increments_for_every_message_including_tombstone(cdc_consumer):
    consumer, _ = cdc_consumer
    before = _counter_value(metrics.EVENTS_RECEIVED_TOTAL, service=metrics.SERVICE_NAME)

    consumer.process_message(_message_with_source("c", 901, after=_sale_row(9002, user_id=1)))
    consumer.process_message(FakeMessage(None, offset=902))

    after = _counter_value(metrics.EVENTS_RECEIVED_TOTAL, service=metrics.SERVICE_NAME)
    assert after == before + 2


def test_failed_counter_increments_on_db_error(cdc_consumer, monkeypatch):
    consumer, _ = cdc_consumer
    before = _counter_value(metrics.EVENTS_FAILED_TOTAL, service=metrics.SERVICE_NAME)

    monkeypatch.setattr(consumer_module, "apply_sale_event", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        consumer.process_message(_message_with_source("c", 903, after=_sale_row(9003, user_id=1)))

    after = _counter_value(metrics.EVENTS_FAILED_TOTAL, service=metrics.SERVICE_NAME)
    assert after == before + 1


def test_retried_counter_increments_on_same_offset_replay(cdc_consumer):
    consumer, _ = cdc_consumer
    msg = _message_with_source("c", 904, after=_sale_row(9004, user_id=1))
    before = _counter_value(metrics.EVENTS_RETRIED_TOTAL, service=metrics.SERVICE_NAME)

    consumer.process_message(msg)
    consumer.process_message(msg)  # same offset again -> replay

    after = _counter_value(metrics.EVENTS_RETRIED_TOTAL, service=metrics.SERVICE_NAME)
    assert after == before + 1


def test_end_to_end_latency_is_observed_from_source_ts_ms(cdc_consumer):
    consumer, _ = cdc_consumer
    sum_before, count_before = _histogram_count(metrics.END_TO_END_LATENCY, service=metrics.SERVICE_NAME)

    source_ts_ms = int(time.time() * 1000) - 2000  # 2 seconds ago
    consumer.process_message(
        _message_with_source("c", 905, after=_sale_row(9005, user_id=1), source_ts_ms=source_ts_ms)
    )

    sum_after, count_after = _histogram_count(metrics.END_TO_END_LATENCY, service=metrics.SERVICE_NAME)
    assert count_after == count_before + 1
    observed_latency = sum_after - sum_before
    assert 1.5 <= observed_latency <= 10  # generous bound for slow CI


def test_db_commit_duration_is_observed_on_success(cdc_consumer):
    consumer, _ = cdc_consumer
    sum_before, count_before = _histogram_count(metrics.DB_COMMIT_DURATION, service=metrics.SERVICE_NAME)

    consumer.process_message(_message_with_source("c", 906, after=_sale_row(9006, user_id=1)))

    sum_after, count_after = _histogram_count(metrics.DB_COMMIT_DURATION, service=metrics.SERVICE_NAME)
    assert count_after == count_before + 1
    assert sum_after >= sum_before
