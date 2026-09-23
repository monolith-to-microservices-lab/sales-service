"""Fixtures for the pure unit layer: SQLite in-memory only, no Postgres, no
Kafka, no network. `_sync_identity_sequence` is Postgres-only raw SQL
(pg_get_serial_sequence/setval) - it is monkeypatched to a no-op here since
sequence realignment is an orthogonal concern to CDC apply correctness and is
already verified for real against Postgres in tests/integration/test_cdc.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cdc import apply as apply_module
from app.database import Base
from app.models import Sale


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def sqlite_session_factory(sqlite_engine):
    return sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False, expire_on_commit=False)


@pytest.fixture
def db_session(sqlite_session_factory):
    session = sqlite_session_factory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _noop_identity_resync(monkeypatch):
    # Patched where it's USED (app.cdc.apply imports the name directly), not
    # where it's defined (app.service).
    monkeypatch.setattr(apply_module, "_sync_identity_sequence", lambda session: None)


def get_sale(session, sale_id: int) -> Sale | None:
    session.expire_all()
    return session.get(Sale, sale_id)
