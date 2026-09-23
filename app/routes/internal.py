"""Internal (migration-only) endpoints.

These are NOT a public API. They exist to import legacy rows from the monolith
while preserving their original ids and timestamps.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from .. import service
from ..database import get_db
from ..schemas import ImportResult, SaleImport, SaleRead

router = APIRouter(prefix="/internal/sales", tags=["internal"])


@router.post("/import", response_model=ImportResult)
def import_sale(
    payload: SaleImport, response: Response, db: Session = Depends(get_db)
) -> ImportResult:
    sale, outcome = service.import_sale(db, payload)
    response.status_code = (
        status.HTTP_201_CREATED if outcome == "created" else status.HTTP_200_OK
    )
    return ImportResult(outcome=outcome, sale=SaleRead.model_validate(sale))
