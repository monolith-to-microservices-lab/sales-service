"""Liveness / readiness probe."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db

router = APIRouter(tags=["health"])
logger = logging.getLogger("sales.health")


@router.get("/health")
def health(db: Session = Depends(get_db)) -> JSONResponse:
    settings = get_settings()
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - report degraded, never crash the probe
        db_ok = False
        logger.exception("health check: database unreachable")

    body = {
        "status": "ok" if db_ok else "degraded",
        "service": settings.service_name,
        "checks": {"database": "up" if db_ok else "down"},
    }
    return JSONResponse(body, status_code=200 if db_ok else 503)
