"""Publish camera frames to LiveKit as one participant with N video tracks (ADR-0018).

A single ``rtc.Room`` (one PeerConnection) carries every camera track — fewer ICE
negotiations / reconnections / transport threads than one room per camera. The
publisher mints its own publish-only token (it holds the LiveKit keys) and pushes
frames into a per-track ``rtc.VideoSource``.
"""

from __future__ import annotations

import asyncio
import logging

import numpy as np
from livekit import api, rtc
from numpy.typing import NDArray

from calibration_service.config import LiveKitConfig
from calibration_service.transport.frame_conversion import bgr_to_video_frame

logger = logging.getLogger(__name__)

# Bitrate budget per (pixel x fps), capped: a high-res/fps stream would otherwise
# request an enormous target bitrate (e.g. 1920x1080@60 ~= 15 Mbps), which makes the
# software VP8 encoder work much harder (CPU-only, ADR-0013). The cap keeps the
# preview sharp for board positioning without that encoder cost.
_BITS_PER_PIXEL = 0.12
_MAX_BITRATE_BPS = 2_000_000


def _max_bitrate(width: int, height: int, fps: int) -> int:
    return min(int(width * height * fps * _BITS_PER_PIXEL), _MAX_BITRATE_BPS)


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
    """Connects to a LiveKit room (one participant) and publishes N camera tracks."""

    def __init__(self) -> None:
        self._room = rtc.Room()
        self._sources: dict[str, rtc.VideoSource] = {}
        self._tracks: dict[str, rtc.LocalVideoTrack] = {}

    async def connect(self, url: str, token: str) -> None:
        await self._room.connect(url, token)
        logger.info("connected to LiveKit room %r", self._room.name)

    async def await_connected(self, timeout: float = 40.0) -> bool:
        """Wait until the WebRTC connection is established (CONN_CONNECTED).

        Publishing a track before the handshake completes triggers codec
        artifacts (noisy/green frames, LiveKit SDK issue #449).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._room.connection_state == rtc.ConnectionState.CONN_CONNECTED:
                return True
            await asyncio.sleep(0.2)
        return self._room.connection_state == rtc.ConnectionState.CONN_CONNECTED

    async def publish_camera_track(self, name: str, width: int, height: int, fps: int) -> None:
        """Publish a camera-sourced video track of the given size/fps under ``name``.

        Published muted, then unmuted by the caller once a first frame is ready —
        avoids the startup codec artifact (issue #449). The encoder is capped at the
        camera fps with a resolution/fps-adapted, ceilinged bitrate. The webapp keys
        the camera tile on this track name (ADR-0018).
        """
        source = rtc.VideoSource(width, height)
        track = rtc.LocalVideoTrack.create_video_track(name, source)
        track.mute()
        options = rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_CAMERA,
            simulcast=False,
            video_encoding=rtc.VideoEncoding(
                max_framerate=float(fps),
                max_bitrate=_max_bitrate(width, height, fps),
            ),
        )
        await self._room.local_participant.publish_track(track, options)
        self._sources[name] = source
        self._tracks[name] = track
        logger.info("published track %r (%dx%d@%d, muted)", name, width, height, fps)

    def unmute(self, name: str) -> None:
        track = self._tracks.get(name)
        if track is not None:
            track.unmute()

    def mute(self, name: str) -> None:
        """Stop sending media for a track without unpublishing it.

        Used for on-demand capture (ADR-0021): when a camera leaves the live set we
        mute its track and close the camera, rather than ``unpublish``-ing — the
        LiveKit Python SDK leaks on frequent unpublish/republish cycles (~200 MB
        each, issue #449). Tracks are published once and stay for the session.
        """
        track = self._tracks.get(name)
        if track is not None:
            track.mute()

    def push(self, name: str, image: NDArray[np.uint8]) -> None:
        """Push a BGR frame to the named track's source.

        ``capture_frame`` is a synchronous, BLOCKING FFI round-trip (~8 ms for a
        960x540 preview: colour conversion + buffer copy in the Rust core).
        """
        source = self._sources.get(name)
        if source is None:
            raise RuntimeError(f"push() for unpublished track {name!r}")
        source.capture_frame(bgr_to_video_frame(image))

    async def send_data(self, payload: str, topic: str) -> None:
        """Publish a data-channel message (best-effort, lossy) to subscribers.

        Used for telemetry ([[coverage-metrics]]): dropping a packet is fine, so it
        goes out unreliable and swallows errors rather than disturbing capture.
        """
        if self._room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
            return
        try:
            await self._room.local_participant.publish_data(payload, reliable=False, topic=topic)
        except Exception:
            logger.debug("data publish failed (topic=%s)", topic, exc_info=True)

    def is_disconnected(self) -> bool:
        """True once the room is fully disconnected (LiveKit gone / gave up reconnecting).

        Stays False while LiveKit's own transient auto-reconnect is in progress
        (CONN_RECONNECTING); flips True only on terminal CONN_DISCONNECTED.
        """
        return self._room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED

    async def aclose(self) -> None:
        await self._room.disconnect()
        self._sources.clear()
        self._tracks.clear()
        logger.info("disconnected from LiveKit")
