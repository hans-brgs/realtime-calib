# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Stage 1 — builder: resolve + install dependencies via uv.
# Same base as the runtime so the copied venv matches the Python/glibc ABI.
# -----------------------------------------------------------------------------
FROM python:3.14-slim-trixie AS builder

# uv from the official Astral image (hermetic, cacheable).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# UV_LINK_MODE=copy: the venv is copied across layers, hard links would break.
# UV_PYTHON=python3.14: lock onto the image interpreter.
ENV UV_LINK_MODE=copy \
    UV_PYTHON=python3.14

# --frozen: use uv.lock as-is. --no-dev: skip ruff/mypy/pytest. --no-install-project:
# calibration-service is an app, not a library; install only the dependencies.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# -----------------------------------------------------------------------------
# Stage 2 — runtime: same base + prebuilt venv + system libs for OpenCV/V4L2.
# -----------------------------------------------------------------------------
FROM python:3.14-slim-trixie

WORKDIR /app

# libgl1 + libglib2.0-0: required by opencv-python. v4l-utils: USB camera support.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    v4l-utils \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Application code (self-contained image; observations/sessions live on a volume).
COPY src /app/src
ENV PYTHONPATH=/app/src

CMD ["python", "-m", "calibration_service.app"]
