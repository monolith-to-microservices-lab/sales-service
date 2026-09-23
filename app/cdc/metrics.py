"""Prometheus metrics for the sales-service-cdc consumer.

Same metric NAMES as user-service-cdc (see
E:\\Projetos\\user-service\\app\\cdc\\metrics.py) so both consumers are
comparable/filterable in Grafana by the `service` label alone - no separate
"sales_*" metric family. No offset/user_id/sale_id ever becomes a label
(unbounded cardinality) - those go in the structured logs instead.
"""

from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram, start_http_server

SERVICE_NAME = "sales-service-cdc"

EVENTS_RECEIVED_TOTAL = Counter(
    "cdc_events_received_total", "Total Kafka messages received", ["service"]
)
EVENTS_PROCESSED_TOTAL = Counter(
    "cdc_events_processed_total",
    "Total CDC events successfully applied, by operation",
    ["service", "operation"],
)
EVENTS_FAILED_TOTAL = Counter(
    "cdc_events_failed_total", "Total CDC events that failed to apply", ["service"]
)
EVENTS_RETRIED_TOTAL = Counter(
    "cdc_events_retried_total",
    "Total events observed at an offset already applied before (redelivery/replay)",
    ["service"],
)
EVENT_PROCESSING_DURATION = Histogram(
    "cdc_event_processing_duration_seconds",
    "Time to process one Kafka message end-to-end within the consumer (parse + apply + commit)",
    ["service"],
)
DB_COMMIT_DURATION = Histogram(
    "cdc_db_commit_duration_seconds",
    "Time spent applying the event to the destination database (including the DB commit)",
    ["service"],
)
KAFKA_OFFSET_COMMIT_TOTAL = Counter(
    "cdc_kafka_offset_commit_total", "Total successful Kafka offset commits", ["service"]
)
KAFKA_OFFSET_COMMIT_FAILED_TOTAL = Counter(
    "cdc_kafka_offset_commit_failed_total", "Total failed Kafka offset commits", ["service"]
)
END_TO_END_LATENCY = Histogram(
    "cdc_end_to_end_latency_seconds",
    "Time from the Debezium source transaction (source.ts_ms) to the destination DB commit",
    ["service"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
LAST_EVENT_TIMESTAMP = Gauge(
    "cdc_last_event_timestamp_seconds", "Unix timestamp of the last event processed", ["service"]
)
LAST_EVENT_OFFSET = Gauge(
    "cdc_last_event_offset",
    "Kafka offset of the last event processed",
    ["service", "topic", "partition"],
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)


def record_end_to_end_latency(source_ts_ms: int | None) -> None:
    if source_ts_ms is None:
        return
    latency = time.time() - (source_ts_ms / 1000)
    if latency >= 0:
        END_TO_END_LATENCY.labels(service=SERVICE_NAME).observe(latency)
