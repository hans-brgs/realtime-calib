"""Publish camera frames to a LiveKit room as a video track (ADR-0004).

Phase 0 publishes one camera track. The publisher mints its own publish-only
token (it holds the LiveKit keys) and pushes frames into an ``rtc.VideoSource``.
"""

from __future__ import annotations

import logging

import numpy as np
from livekit import api, rtc
from numpy.typing import NDArray

from calibration_service.config import LiveKitConfig
from calibration_service.transport.frame_conversion import bgr_to_video_frame

logger = logging.getLogger(__name__)


def mint_publish_token(config: LiveKitConfig, identity: str, room: str) -> str:
    """Mint a publish-only LiveKit access token for the service."""
    grants = api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=False,
    )
    return (
        api.AccessToken(config.api_key, config.api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(grants)
        .to_jwt()
    )


class LiveKitPublisher:
    """Connects to a LiveKit room and publishes a single camera video track."""

    def __init__(self) -> None:
        self._room = rtc.Room()
        self._source: rtc.VideoSource | None = None
        self._track: rtc.LocalVideoTrack | None = None

    async def connect(self, url: str, token: str) -> None:
        await self._room.connect(url, token)
        logger.info("connected to LiveKit room %r", self._room.name)

    async def publish_camera_track(self, name: str, width: int, height: int) -> None:
        """Create and publish a camera-sourced video track of the given size."""
        source = rtc.VideoSource(width, height)
        track = rtc.LocalVideoTrack.create_video_track(name, source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        await self._room.local_participant.publish_track(track, options)
        self._source = source
        self._track = track
        logger.info("published track %r (%dx%d)", name, width, height)

    def push(self, image: NDArray[np.uint8]) -> None:
        """Push a BGR frame to the published track. No-op if not publishing yet."""
        if self._source is None:
            raise RuntimeError("push() called before publish_camera_track()")
        self._source.capture_frame(bgr_to_video_frame(image))

    async def aclose(self) -> None:
        await self._room.disconnect()
        logger.info("disconnected from LiveKit")
