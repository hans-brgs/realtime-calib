# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Stage 1 — builder: resolve + install dependencies via uv.
# -----------------------------------------------------------------------------
FROM python:3.14-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_PYTHON=python3.14

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# -----------------------------------------------------------------------------
# Stage 2 — runtime: prebuilt venv (Flask + gunicorn + livekit-api are pure Python).
# -----------------------------------------------------------------------------
FROM python:3.14-slim-trixie

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY src /app/src
COPY gunicorn.conf.py /app/gunicorn.conf.py
ENV PYTHONPATH=/app/src

# Gunicorn loads gunicorn.conf.py then serves the app from the factory.
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", "livekit_token_server.app:create_app()"]
