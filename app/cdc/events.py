"""Parsing for Debezium change events on `legacy.public.sales`.

Same connector config as `legacy.public.users` (schemas.enable=false, no
unwrap SMT - see cdc-infrastructure/debezium/register-connector.json), so a
Kafka value is exactly the raw Debezium envelope: `{before, after, source,
op, ts_ms, transaction}`. Confirmed against a real captured event (see
cdc-infrastructure/scripts/inspect-sales.sh) before writing this model -
field names below are not guessed.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SaleCdcPayload(BaseModel):
    """Shape of `before` / `after` for the `sales` table.

    Matches the legacy monolith's `sales` columns 1:1 (id, user_id, item_name,
    quantity, created_at) - see the schema comparison in this change's notes.
    `user_id` is carried through as a plain value: the Sales Service's own
    `Sale` model deliberately has no foreign key to a local `users` table (it
    lives in a different database/service), so there is nothing to validate
    against locally, and no synchronous call to user-service is made here.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    user_id: int
    item_name: str
    quantity: int
    created_at: datetime


class DebeziumSource(BaseModel):
    """The subset of Debezium's `source` block used for CDC observability.

    `ts_ms` is the timestamp of the transaction commit in the *source*
    Postgres database (from the WAL record) - used to compute end-to-end CDC
    latency. Distinct from the envelope's top-level `ts_ms` (when Kafka
    Connect processed the record).
    """

    model_config = ConfigDict(extra="ignore")

    ts_ms: int | None = None


class DebeziumSaleEnvelope(BaseModel):
    """A single Kafka value from `legacy.public.sales`.

    `op`: "c" (create), "r" (snapshot read - the connector runs with
    `snapshot.mode=no_data`, so this is never produced by this pipeline, but
    is handled defensively like a create), "u" (update), "d" (delete).
    Deletes are followed by a separate tombstone message (Kafka value =
    null), which never reaches this model - the consumer handles that case
    before parsing.
    """

    model_config = ConfigDict(extra="ignore")

    op: str
    before: SaleCdcPayload | None = None
    after: SaleCdcPayload | None = None
    source: DebeziumSource | None = None
    ts_ms: int | None = None
