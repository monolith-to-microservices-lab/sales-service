# sales-service

Second microservice extracted from the legacy monolith as part of a **gradual
Strangler Fig migration**. This repository is **exclusively** the Sales Service:
its own code, its own database, its own migrations, its own Docker setup, its own
lifecycle.

It does **not** manage, import, or connect to the legacy monolith or the User
Service.

---

## Current architecture (this stage)

```text
                        FRONTEND
                           |
                           v
                        MONOLITH            <-- still the ACTIVE system
                           |
                           v
                   Legacy PostgreSQL
                    |             |
                  users         sales


Being prepared in parallel (independent repos, independent DBs):

  USER SERVICE                       SALES SERVICE  (this repo)
       |                                   |
       v                                   v
  User PostgreSQL                    Sales PostgreSQL
                                           |
                                         sales
```

```text
sales-service
      |
      v
sales-postgres        <-- the ONLY database this service talks to
```

There is **no** connection such as:

```text
sales-service ---X---> legacy-postgres
sales-service ---X---> user-postgres
```

No synchronization between databases exists yet. The legacy DB remains the
source of truth. `User DB` and `Sales DB` are *prepared* but not *active*.

---

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Pydantic v2 · PostgreSQL 16 · Alembic ·
Docker / Docker Compose.

Layering is deliberately shallow (no repository layer):

```text
route  ->  service  ->  SQLAlchemy  ->  Sales PostgreSQL
```

---

## Project layout

```text
sales-service/
├── app/
│   ├── main.py              # FastAPI app, lifespan, exception handlers
│   ├── config.py            # env-var settings (pydantic-settings)
│   ├── database.py          # engine / session / Base
│   ├── models.py            # Sale model
│   ├── schemas.py           # Pydantic v2 request/response models
│   ├── service.py           # business logic + import + sequence realignment
│   ├── exceptions.py        # domain errors -> HTTP
│   ├── logging_config.py    # JSON logs + correlation id
│   ├── middleware.py        # correlation/request id + request logging
│   └── routes/
│       ├── sales.py         # public CRUD  (/sales)
│       ├── internal.py      # migration-only (/internal/sales/import)
│       └── health.py        # /health
├── migrations/              # Alembic (env.py + versions/0001_create_sales.py)
├── tests/                   # pytest (16 tests, real PostgreSQL)
├── docker/entrypoint.sh     # runs `alembic upgrade head` then the CMD
├── Dockerfile
├── docker-compose.yml       # sales-service + sales-postgres ONLY
├── alembic.ini
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Configuration

All configuration comes from environment variables. No secrets are hardcoded.
Copy `.env.example` to `.env`.

| Variable       | Purpose                                        | Example                                                      |
|----------------|------------------------------------------------|-------------------------------------------------------------|
| `DATABASE_URL` | Connection to the Sales Service's own Postgres | `postgresql+psycopg2://sales:sales@sales-postgres:5432/sales` |
| `APP_ENV`      | `development` / `staging` / `production`        | `development`                                               |
| `LOG_LEVEL`    | `DEBUG` … `CRITICAL`                            | `INFO`                                                      |
| `SERVICE_NAME` | Logical name in logs / health output           | `sales-service`                                             |

---

## Running with Docker

```bash
docker compose up --build
```

Brings up exactly two containers:

```text
Docker Compose
    ├── sales-service     (FastAPI, host port 8080 -> container 8000)
    └── sales-postgres    (PostgreSQL 16, host port 5434 -> container 5432)
```

Migrations run automatically on container start (`docker/entrypoint.sh`).

Health check:

```bash
curl -s localhost:8080/health
# {"status":"ok","service":"sales-service","checks":{"database":"up"}}
```

---

## Running locally (without Docker for the app)

```bash
python -m venv .venv && . .venv/Scripts/activate      # or bin/activate on *nix
pip install -e ".[dev]"

docker compose up -d sales-postgres                    # just the DB
export DATABASE_URL=postgresql+psycopg2://sales:sales@localhost:5434/sales

alembic upgrade head
uvicorn app.main:app --reload
```

### Tests

```bash
docker compose up -d sales-postgres
export DATABASE_URL=postgresql+psycopg2://sales:sales@localhost:5434/sales
pytest
```

Tests run against a **real PostgreSQL** (identity columns and sequences are
Postgres-specific and must be exercised faithfully). The schema is created via
Alembic; each test truncates `sales` with `RESTART IDENTITY`.

---

## Endpoints

| Method | Path                     | Purpose                                             |
|--------|--------------------------|-----------------------------------------------------|
| GET    | `/health`                | Liveness/readiness; 200 ok / 503 degraded           |
| POST   | `/sales`                 | Create a sale natively (id assigned by Postgres)    |
| GET    | `/sales`                 | List sales (`?limit=&offset=`)                      |
| GET    | `/sales/{id}`            | Get one sale (404 if missing)                       |
| PUT    | `/sales/{id}`            | Full update of `user_id`, `item_name`, `quantity`   |
| DELETE | `/sales/{id}`            | Delete a sale (204; 404 if missing)                 |
| POST   | `/internal/sales/import` | **Migration only** — import a legacy sale by id     |

Response contract:

```json
{
  "id": 10,
  "user_id": 37,
  "item_name": "Perfume",
  "quantity": 2,
  "created_at": "2026-09-08T12:00:00+00:00"
}
```

Import response is wrapped so callers can tell what happened:

```json
{ "outcome": "created", "sale": { "id": 500, "user_id": 37, "item_name": "Perfume", "quantity": 2, "created_at": "2023-01-15T10:30:00+00:00" } }
```

### Local validation only

Pydantic v2 enforces **local** rules only:

* `user_id` — required, integer `>= 1`
* `item_name` — required, non-blank, `<= 255` chars (trimmed)
* `quantity` — required, integer `> 0` (also a DB `CHECK` constraint)

The service does **not** call the User Service to verify `user_id` (see below).

### Observability / lifecycle

* Structured JSON logs on stdout.
* Correlation id per request: `X-Request-ID` header is honored if present,
  otherwise a UUID is generated; it is echoed back on the response and attached
  to every log line.
* Graceful shutdown: the FastAPI lifespan disposes the DB connection pool;
  uvicorn runs with `--timeout-graceful-shutdown 20` and compose gives the
  container a 25s stop grace period.

---

## Database

`sales` table (owned entirely by this service):

```text
Column      | Type                      | Notes
------------+---------------------------+---------------------------------------
id          | bigint                    | PRIMARY KEY, GENERATED BY DEFAULT AS IDENTITY
user_id     | integer                   | NOT NULL, indexed — logical ref only, NO FK
item_name   | character varying(255)    | NOT NULL
quantity    | integer                   | NOT NULL, CHECK (quantity > 0)
created_at  | timestamp with time zone  | NOT NULL, DEFAULT now()
```

Types mirror the legacy monolith (`id`/`user_id`/`quantity` integer,
`item_name` varchar, `created_at` timestamp). No extra columns were invented.

---

## Referential Integrity

In the monolith, both domains lived in one database, so the relationship was a
**physical foreign key** enforced by PostgreSQL:

```text
sales.user_id  ──FK──▶  users.id      (guaranteed by the legacy DB)
```

Once Sales and User live in **separate databases**, that foreign key **cannot
exist** — PostgreSQL has no cross-database foreign keys, and cross-service FKs
are an anti-pattern in a microservice architecture. So in this repo:

```text
Sales DB                         User DB
sales.user_id  ····logical····▶  users.id
       (a plain integer)          (owned by the User Service)
```

`user_id` here is **just an identifier**. The Sales database can no longer
guarantee that `user_id = 37` refers to a real user. Example:

```text
Sales Service            User Service
  user_id = 37   ───?───▶   user id = 37   (may or may not exist)
```

**This stage does not address that gap on purpose.** Even now that CDC
ingestion exists (see below), there is still:

* no HTTP / gRPC call to the User Service to validate a `user_id`
* no user cache, no user replica table in this database
* no distributed / cross-service validation, and no ordering dependency on
  the `legacy.public.users` topic having been processed first

The point of this stage is to make the consequence of splitting the databases
**explicit and observable**: *the Sales DB can no longer prove, via a foreign
key, that `user_id` exists.* The strategy to handle it (sync API call with
graceful degradation, replicated read model, eventual-consistency events,
periodic reconciliation, …) will be **decided and implemented in a later
stage**. This is tested explicitly:
`test_sale_referencing_user_not_locally_known_is_applied_without_validation`.

---

## CDC consumer (`legacy.public.sales` -> Sales DB)

A separate process, `python -m app.cdc`, keeps this service's `sales` table in
sync with the legacy monolith by consuming Debezium change events produced by
the `cdc-infrastructure` repo. It never runs inside the FastAPI process and
never changes the HTTP behavior described above. Structurally identical to
`user-service`'s `app/cdc` (same offset/idempotency/commit semantics) - only
the entity and its fields differ.

```text
legacy-postgres (monolith)
   │  WAL
   ▼
Debezium / Kafka Connect  ──►  topic legacy.public.sales
                                        │
                                        ▼
                          app/cdc  (this repo, separate process)
                                        │
                                        ▼
                             Sales PostgreSQL (sales)
```

- **`op` handling**: `c` / `r` / `u` → upsert by legacy `id` (reuses
  `service._sync_identity_sequence`, so a normal `POST /sales` afterwards
  still gets a fresh, non-colliding id). `d` → delete if present; a no-op if
  the row is already gone. This is a plain idempotent upsert, deliberately
  **not** the conflict-raising semantics of `service.import_sale` (which is
  correct for a one-time migration snapshot, but wrong for CDC: an `update`
  event must overwrite the destination row, never conflict with it).
- **Idempotency**: upsert-by-id and delete-if-present mean replaying the same
  event (or the whole topic from scratch) converges to the same state - no
  duplicate rows.
- **Offsets**: `enable.auto.commit=False`. The Kafka offset is committed only
  after the database transaction for that message has committed. A database
  failure raises, skips the offset commit, and stops the process; on restart
  Kafka redelivers from the last committed offset, safely re-applied thanks to
  idempotency. No in-process retry - failures fail fast and rely on the
  process being restarted (`restart: unless-stopped` in compose).
- **Tombstones**: the delete-marker message that follows every `d` event
  (Kafka value `null`) is skipped and its offset is committed immediately.
- **user_id**: carried through as a plain value, never validated against
  user-service (see "Referential Integrity" above) - no synchronous call, no
  blocking, no ordering dependency between the `users` and `sales` topics.
- **Metrics**: Prometheus `/metrics` on `CDC_METRICS_PORT` (default `9201`) -
  `cdc_events_received_total`, `cdc_events_processed_total{operation=...}`,
  `cdc_events_failed_total`, `cdc_event_processing_duration_seconds`,
  `cdc_db_commit_duration_seconds`, `cdc_end_to_end_latency_seconds` (from
  Debezium's `source.ts_ms`), `cdc_kafka_offset_commit_total(_failed)`,
  `cdc_events_retried_total`. Same metric *names* as `user-service-cdc` (only
  the `service` label differs), so both show up together in
  `observability-infrastructure`'s Grafana "CDC Consumer" dashboard.
- **Tracing**: OpenTelemetry spans `sales.cdc.process` (parent),
  `sales.cdc.db.apply`, `sales.cdc.kafka.commit`, exported to the OTel
  Collector / Tempo in `observability-infrastructure`. This is a fresh root
  trace per message - it does **not** continue a trace from the monolith's
  HTTP request, because Debezium reads the WAL, not application context (see
  that repo's README for why). Correlation across that gap is via
  `topic`/`partition`/`offset`/`sale_id`/`user_id`/`source.ts_ms` in the
  structured logs, not a shared `trace_id`.
- **Logs**: structured JSON (same `JsonFormatter` as the HTTP server), one
  `cdc.applied` / `cdc.apply_failed` / `cdc.tombstone_skipped` line per
  message with `topic`, `partition`, `offset`, and - except for tombstones -
  `op`, `sale_id`, `user_id`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker (host-published port from `cdc-infrastructure`; use `kafka:9092` inside docker-compose) |
| `KAFKA_SALES_TOPIC` | `legacy.public.sales` | Source topic |
| `KAFKA_CONSUMER_GROUP` | `sales-service-cdc` | Kafka consumer group id |
| `KAFKA_AUTO_OFFSET_RESET` | `earliest` | Where to start if the group has no committed offset yet |
| `CDC_METRICS_PORT` | `9201` | Prometheus `/metrics` port for this process |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | Where traces are sent |

### Running it

```bash
# via docker compose, alongside sales-service and sales-postgres
docker compose up -d --build sales-service-cdc

# locally (needs cdc-infrastructure's Kafka reachable at localhost:9092
# and sales-postgres reachable at localhost:5434 - defaults already cover both)
.venv\Scripts\activate            # Windows
pip install -e ".[dev]"
python -m app.cdc
```

### Tests

`tests/test_cdc.py` and `tests/test_cdc_metrics.py` cover create/update/
delete/tombstone, idempotent reprocessing, restart redelivery, a DB-error
path (offset must not advance), an out-of-order event, the "unknown user_id"
case, and metric/latency instrumentation - using in-process fakes for Kafka,
against the isolated `sales_test` database that `tests/conftest.py` points at
by default (never the dev database on port 5434 - `conftest.py` refuses to
run at all against a database whose name doesn't contain `test`).

---

## Migration: preserving legacy IDs

Legacy sales must keep their original id when moved into this service. A sale
that is `id = 500` in the monolith stays `id = 500` here.

### Two explicitly separated flows

| Flow            | Endpoint                 | `id` source            |
|-----------------|--------------------------|------------------------|
| Normal creation | `POST /sales`            | PostgreSQL IDENTITY    |
| Legacy import   | `POST /internal/sales/import` | Supplied by caller, preserved verbatim |

`created_at` is likewise preserved on import (the caller must send it).

### Idempotency of import

`POST /internal/sales/import` is idempotent, keyed on `id`:

| Situation                                   | Result                              |
|---------------------------------------------|-------------------------------------|
| `id` does not exist                         | insert → `201` `{"outcome":"created"}` |
| `id` exists, **same** data                  | no-op → `200` `{"outcome":"unchanged"}` |
| `id` exists, **different** data             | `409 Conflict` (never silently overwritten) |

"Same data" compares `user_id`, `item_name`, `quantity`, and `created_at`
(compared as the same UTC instant). A `409` body includes `current` vs.
`incoming` so the migration operator can reconcile.

### Keeping the IDENTITY sequence consistent

When you `INSERT` an explicit `id` into a `GENERATED BY DEFAULT AS IDENTITY`
column, PostgreSQL **does not advance** the backing sequence. Left alone, this
happens:

```text
import id = 1000        -> row 1000 exists, sequence still at 1
POST /sales             -> nextval = 1  -> tries id = 1
... later ...
POST /sales (x1000)     -> eventually nextval = 1000 -> COLLISION with the import
```

After **every** explicit-id insert, `app/service.py::_sync_identity_sequence`
runs:

```sql
SELECT setval(pg_get_serial_sequence('sales', 'id'), (SELECT MAX(id) FROM sales));
```

This sets the sequence to `MAX(id)` with `is_called = true`, so the next
`POST /sales` produces `MAX(id) + 1`. Native creation never reuses or collides
with an imported id. This is covered by the test
`test_native_create_after_high_id_import_does_not_collide`.

Column is `GENERATED BY DEFAULT AS IDENTITY` (not `ALWAYS`) precisely so that
explicit-id imports are allowed without `OVERRIDING SYSTEM VALUE`.

---

## Migration state

```text
LEGACY
  Legacy PostgreSQL
    ├── users      (active, source of truth)
    └── sales      (active, source of truth)

NEW
  User Service  -> User PostgreSQL    (CDC consumer active)
  Sales Service -> Sales PostgreSQL   (CDC consumer active)   <-- this repo
```

* Legacy DB = still the active source of truth.
* User DB and Sales DB = kept in sync incrementally by their own CDC
  consumers (`user-service-cdc`, `sales-service-cdc`); the initial bulk
  migration (`migration-tool`) only ran once, before CDC existed.

---

## Explicitly NOT implemented in this stage

Synchronous communication with the User Service (HTTP/gRPC), Saga, Outbox,
Redis, API Gateway, Kubernetes, service mesh, complex auth, distributed
cache, dual-write, monolith sync (this service never writes back to the
legacy DB), cross-DB sync, frontend changes, removal of Sales from the
monolith, traffic cutover. (CDC/Debezium via Kafka *is* now implemented -
see "CDC consumer" above - one-directional, legacy -> this service, only.)

Those come in later stages, after the three systems are reviewed in isolation.

## CI

Every pull request to `main` (and every push to `main`) runs, with no deploy and no cloud credentials:

| Workflow | Job | What it proves |
|---|---|---|
| `ci.yml` | **Lint** | `ruff check`, `ruff format --check`, Hadolint (Dockerfile), ShellCheck (entrypoint) |
| | **Type Check** | `mypy` (no global ignores, no per-module overrides) |
| | **Unit Tests** | `pytest -m unit` + coverage + JUnit report |
| | **Integration Tests** | `alembic upgrade head` on a fresh DB, then `pytest -m integration` against a **real PostgreSQL 16** (`sales_test`) and a **real Kafka 3.9.1** (throw-away topics/consumer groups) |
| | **Build** | `docker build`, image runs as non-root (UID 1000), Trivy image scan (HIGH/CRITICAL with a fix fails), `docker compose config` |
| `security.yml` | **Security** | Gitleaks (full history, redacted), Bandit (`app/`), pip-audit (declared dependencies), Trivy config (SARIF → Code Scanning). Also weekly. |

Coverage baseline when CI was introduced: unit 68%, integration 80%. No threshold is enforced yet.

Run the same checks locally:

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest -m unit
TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/sales_test pytest -m integration   # needs Postgres + Kafka on localhost:9092
```
