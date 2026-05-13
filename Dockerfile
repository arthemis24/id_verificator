# ── Stage 1: dependencies ────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System libs needed to compile Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install application dependencies from requirements.txt (authoritative source)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for the app process
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Runtime system dependencies only (no build tools, no git)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    tesseract-ocr \
    tesseract-ocr-fra \
    tesseract-ocr-eng \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appgroup . /app

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TF_USE_LEGACY_KERAS=1

# Media uploads directory owned by the app user
RUN mkdir -p /app/media && chown appuser:appgroup /app/media

USER appuser

# Gunicorn is the production server — runserver is dev-only
CMD ["gunicorn", "id_verificator.wsgi:application", "--bind", "0.0.0.0:8001", "--workers", "2", "--timeout", "120"]
