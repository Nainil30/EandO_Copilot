# syntax=docker/dockerfile:1.7
# One image, two entrypoints (API + UI) – see docker-compose.yml.

FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY ui ./ui
COPY docs ./docs
COPY db ./db
COPY scripts ./scripts
COPY evals ./evals

# run as non-root
RUN useradd --create-home appuser && mkdir -p /app/.chroma /app/.cache /app/.logs /app/data \
    && chown -R appuser /app
USER appuser

EXPOSE 8000 8501
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD curl -fs http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
