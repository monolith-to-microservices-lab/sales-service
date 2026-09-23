"""FastAPI application factory, wiring, and lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import get_settings
from .database import engine
from .exceptions import ImportConflictError, NotFoundError
from .logging_config import configure_logging, correlation_id_var
from .middleware import RequestContextMiddleware
from .observability import instrument_app, instrument_db_metrics
from .routes import health, internal, sales

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("sales.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"app_env": settings.app_env})
    yield
    # Graceful shutdown: drain the connection pool.
    logger.info("shutdown: disposing database engine")
    engine.dispose()
    logger.info("shutdown: complete")


app = FastAPI(
    title="sales-service",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware)
instrument_app(app)
instrument_db_metrics(engine)

app.include_router(health.router)
app.include_router(sales.router)
app.include_router(internal.router)


def _error(status_code: int, detail: str, **extra) -> JSONResponse:
    body = {"detail": detail, "correlation_id": correlation_id_var.get()}
    body.update(extra)
    return JSONResponse(body, status_code=status_code)


@app.exception_handler(NotFoundError)
async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return _error(404, str(exc))


@app.exception_handler(ImportConflictError)
async def _import_conflict(request: Request, exc: ImportConflictError) -> JSONResponse:
    return _error(
        409,
        str(exc),
        sale_id=exc.sale_id,
        current=exc.current,
        incoming=exc.incoming,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(422, "validation error", errors=jsonable_encoder(exc.errors()))


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return _error(500, "internal server error")
