"""Unit tests for BGR -> RGBA / VideoFrame conversion (no LiveKit server needed)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from calibration_service.config import LiveKitConfig
from calibration_service.transport.frame_conversion import bgr_to_rgba, bgr_to_video_frame
from calibration_service.transport.livekit_publisher import mint_publish_token


def _bgr_pixel(b: int, g: int, r: int) -> NDArray[np.uint8]:
    return np.array([[[b, g, r]]], dtype=np.uint8)


def test_bgr_to_rgba_reorders_channels_and_adds_opaque_alpha() -> None:
    rgba = bgr_to_rgba(_bgr_pixel(b=10, g=20, r=30))
    assert rgba.shape == (1, 1, 4)
    assert tuple(int(v) for v in rgba[0, 0]) == (30, 20, 10, 255)


def test_bgr_to_video_frame_preserves_dimensions() -> None:
    image = np.zeros((48, 64, 3), dtype=np.uint8)
    frame = bgr_to_video_frame(image)
    assert frame.width == 64
    assert frame.height == 48


def test_mint_publish_token_grants_publish_only() -> None:
    import jwt

    config = LiveKitConfig(api_key="devkey", api_secret="test-secret-32-bytes-long-aaaaaaaa")
    token = mint_publish_token(config, identity="service-cam_0", room="calibration")

    claims = jwt.decode(token, "test-secret-32-bytes-long-aaaaaaaa", algorithms=["HS256"])
    assert claims["sub"] == "service-cam_0"
    assert claims["video"]["room"] == "calibration"
    assert claims["video"]["canPublish"] is True
    assert claims["video"].get("canSubscribe") in (False, None)
