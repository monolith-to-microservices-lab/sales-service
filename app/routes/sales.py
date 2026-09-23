"""Public Sales CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from .. import service
from ..database import get_db
from ..schemas import SaleCreate, SaleRead, SaleUpdate

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("", response_model=SaleRead, status_code=status.HTTP_201_CREATED)
def create_sale(payload: SaleCreate, db: Session = Depends(get_db)) -> SaleRead:
    return SaleRead.model_validate(service.create_sale(db, payload))


@router.get("", response_model=list[SaleRead])
def list_sales(
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[SaleRead]:
    return [SaleRead.model_validate(s) for s in service.list_sales(db, limit, offset)]


@router.get("/{sale_id}", response_model=SaleRead)
def get_sale(sale_id: int, db: Session = Depends(get_db)) -> SaleRead:
    return SaleRead.model_validate(service.get_sale(db, sale_id))


@router.put("/{sale_id}", response_model=SaleRead)
def update_sale(sale_id: int, payload: SaleUpdate, db: Session = Depends(get_db)) -> SaleRead:
    return SaleRead.model_validate(service.update_sale(db, sale_id, payload))


@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sale(sale_id: int, db: Session = Depends(get_db)) -> Response:
    service.delete_sale(db, sale_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
