FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker/entrypoint.sh ./docker/entrypoint.sh

RUN pip install . && chmod +x ./docker/entrypoint.sh

# Non-root runtime user (numeric UID so Kubernetes/ECS can verify it).
RUN useradd --create-home --uid 1000 appuser && chown -R 1000:1000 /app
USER 1000

EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
# --timeout-graceful-shutdown lets in-flight requests finish on SIGTERM.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "20"]
