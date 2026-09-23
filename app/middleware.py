"""Correlation-id / request-logging middleware."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from .logging_config import correlation_id_var

logger = logging.getLogger("sales.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)
        start = time.perf_counter()

        logger.info(
            "request.start",
            extra={"method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "request.error",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": elapsed_ms,
                },
            )
            raise
        finally:
            correlation_id_var.reset(token)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = correlation_id
        logger.info(
            "request.end",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
            },
        )
        return response
