"""HTTP API entry point for the calibration service.

Long-running process orchestrated by the HTTP API (ADR-0004/0005). The FastAPI
lifespan starts the camera publishing loop (ADR-0003/0004): every detected
camera is published as a LiveKit video track for the webapp preview.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from calibration_service import __version__
from calibration_service.config import Config, LiveKitConfig
from calibration_service.logging_setup import setup_logging
from calibration_service.transport.camera_publish_service import CameraPublishService

logger = logging.getLogger(__name__)

SERVICE_NAME = "calibration-service"


class HealthResponse(BaseModel):
    """Liveness payload returned by ``GET /health``."""

    status: str
    service: str
    version: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    config = Config()
    logger.info("calibration-service starting (sessions_dir=%s)", config.sessions_dir)

    publish_service = CameraPublishService(LiveKitConfig())
    await publish_service.start()
    try:
        yield
    finally:
        await publish_service.stop()


def create_app() -> FastAPI:
    """Build the FastAPI application (factory, so tests get a fresh instance)."""
    app = FastAPI(title=SERVICE_NAME, version=__version__, lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME, version=__version__)

    return app


app = create_app()


def main() -> None:
    """Run the service with uvicorn, bound to the configured host/port."""
    import uvicorn

    setup_logging()
    config = Config()
    logger.info("Starting %s on %s:%d", SERVICE_NAME, config.http_host, config.http_port)
    uvicorn.run(app, host=config.http_host, port=config.http_port)


if __name__ == "__main__":
    main()
