"""Fixtures for the real-Postgres integration layer. Everything here touches
the actual `sales_test` database (schema upgrade/downgrade + TRUNCATE between
tests) - kept out of the root conftest.py so tests/unit/** never pays that
cost or that risk.
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal, engine
from app.main import app
from tests.conftest import assert_test_database


@pytest.fixture(scope="session", autouse=True)
def _schema():
    assert_test_database(str(engine.url))
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture(autouse=True)
def _clean_table():
    assert_test_database(str(engine.url))
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE sales RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session():
    session = SessionLocal()
    yield session
    session.close()
