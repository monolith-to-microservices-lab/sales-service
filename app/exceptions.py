"""Domain-level exceptions, mapped to HTTP responses in app.main."""

from __future__ import annotations


class NotFoundError(Exception):
    def __init__(self, sale_id: int) -> None:
        self.sale_id = sale_id
        super().__init__(f"Sale {sale_id} not found")


class ImportConflictError(Exception):
    """A sale with the same id already exists but with different data."""

    def __init__(self, sale_id: int, current: dict, incoming: dict) -> None:
        self.sale_id = sale_id
        self.current = current
        self.incoming = incoming
        super().__init__(f"Sale {sale_id} already exists with different data (import conflict)")
