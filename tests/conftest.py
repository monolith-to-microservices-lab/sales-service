from __future__ import annotations

import os

# Tests run against their OWN database so they never disturb the dev/compose
# data. Override with TEST_DATABASE_URL if needed. Set here (root conftest)
# because it must happen before ANY `app.*` import.
#
# HISTORY: this used to default to the same DB as the dev docker-compose
# stack, and an autouse fixture TRUNCATEd it before every test - which wiped
# 1002 real rows of lab data once. Fixed: isolated `sales_test` database
# (mirroring user-service's pattern) plus the explicit guard below.
_DEFAULT_TEST_DB = "postgresql+psycopg2://sales:sales@localhost:5434/sales_test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_DB)
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("APP_ENV", "test")

import pytest  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def assert_test_database(url: str) -> None:
    """Defense in depth: even if TEST_DATABASE_URL is overridden by whoever
    runs the suite, refuse to run against anything that isn't unambiguously a
    test database. tests/integration TRUNCATEs and drops/recreates schema.
    Unit tests never touch Postgres at all (see tests/unit/conftest.py).
    """
    db_name = make_url(url).database or ""
    if "test" not in db_name.lower():
        raise RuntimeError(
            f"Refusing to run tests against database {db_name!r} - its name does not "
            "contain 'test'. Set TEST_DATABASE_URL to a database whose name contains "
            "'test' (e.g. 'sales_test') before running pytest."
        )


assert_test_database(os.environ["DATABASE_URL"])


def pytest_collection_modifyitems(config, items):
    """Auto-mark by directory instead of hand-annotating every test file."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)
