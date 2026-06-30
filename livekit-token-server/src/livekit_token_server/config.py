"""Settings loaded from the environment (LiveKit keys are secrets, never hardcoded)."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ROOM_NAME = "calibration"
DEFAULT_PORT = 8080


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the token server."""

    livekit_api_key: str
    livekit_api_secret: str
    room_name: str
    port: int


def load_settings() -> Settings:
    """Build ``Settings`` from environment variables, failing fast on missing keys."""
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")

    return Settings(
        livekit_api_key=api_key,
        livekit_api_secret=api_secret,
        room_name=os.environ.get("LIVEKIT_ROOM_NAME", DEFAULT_ROOM_NAME),
        port=int(os.environ.get("TOKEN_SERVER_PORT", str(DEFAULT_PORT))),
    )
