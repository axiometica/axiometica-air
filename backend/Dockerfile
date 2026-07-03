# ─────────────────────────────────────────────────────────────────────────────
# Backend — two-stage image
#
# Stage 1 (builder): installs all Python dependencies using gcc / build tools.
#                    Output: a virtual env at /opt/venv that is ~200 MB smaller
#                    than the full dev image (no gcc, mypy, black, pytest, etc.).
#
# Stage 2 (runtime): slim image — copies only the venv + source; runs as a
#                    non-root appuser (UID 1001) to limit blast radius.
#
# Dev hot-reload: docker-compose.override.yml bind-mounts ./backend/src over
# /app/src so edits are reflected without rebuilding the image.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Build-time system deps (gcc required by some Python packages — psycopg2-binary,
# cryptography, etc.).  These stay in the builder layer only.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Isolate deps in a virtual env so we can COPY just the venv to the runtime image
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Only runtime deps — dev tools (pytest, black, mypy) are in requirements-dev.txt
# and are never installed in the production image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim

# Upgrade all base packages first so the image ships with latest security
# patches (e.g. libxml2 CVE-2026-6732).  Done in a single RUN layer to keep
# image size minimal.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

# Non-root user reduces blast radius if the application is compromised
RUN useradd --system --uid 1001 --gid 0 \
    --create-home --home-dir /home/appuser \
    --shell /sbin/nologin appuser

WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source code (baked into image; overridden by dev volume mount in override.yml)
COPY src/ ./src/

# Copy workflow definitions, seed data, migrations, and Alembic config
COPY workflows/    /app/workflows/
COPY seeds/        /app/seeds/
COPY migrations/   /app/migrations/
COPY alembic.ini   /app/alembic.ini

# Copy out-of-box setup script (idempotent seeding for fresh installs)
COPY setup_oob.py  /app/setup_oob.py

# Pre-create named-volume mount points so Docker preserves the correct
# ownership when it initialises the volume on first container start.
# Without this, Docker creates the mount point as root:root and the
# non-root appuser cannot write the Celery beat schedule file.
RUN mkdir -p /app/.beat

# Give appuser ownership of everything under /app
RUN chown -R appuser:root /app && chmod -R g=u /app

# Python runtime settings
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Drop privileges — the rest of the container lifecycle runs as appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "agentic_os.main:app", "--host", "0.0.0.0", "--port", "8000"]
