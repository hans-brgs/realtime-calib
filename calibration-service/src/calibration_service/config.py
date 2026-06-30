"""Service configuration, sourced from ``CALIB_*`` environment variables.

This ``Config`` class is the single source of truth for runtime settings
(see the service CLAUDE.md). Values come from the environment (propagated by
docker-compose from the root ``.env``); defaults make same-machine dev work
out of the box.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Runtime configuration read from ``CALIB_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="CALIB_", extra="ignore")

    # HTTP API (commands/config).
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # Root of the calibration session folders = source of truth (ADR-0011).
    sessions_dir: Path = Path("/data/sessions")

    # Camera capture backend (V4L2 on Linux).
    camera_backend: str = "v4l2"


class LiveKitConfig(BaseSettings):
    """LiveKit connection settings, read from ``LIVEKIT_*`` environment variables.

    Defaults match ``livekit-server --dev`` (well-known dev key/secret) so local
    same-machine runs work out of the box; production overrides via env.
    """

    model_config = SettingsConfigDict(env_prefix="LIVEKIT_", extra="ignore")

    url: str = "ws://localhost:7880"
    api_key: str = "devkey"
    api_secret: str = "secret"
    room_name: str = "calibration"
