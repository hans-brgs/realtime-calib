"""Telemetry payload tests."""

from __future__ import annotations

import numpy as np

from calibration_service.detection import BoardDetection
from calibration_service.telemetry import SHARPNESS_MIN, coverage_metrics_payload


def _detection(sharpness: float) -> BoardDetection:
    corners = np.zeros((42, 2), np.float32)
    return BoardDetection(
        found=True,
        corners=corners,
        ids=np.arange(42, dtype=np.int32),
        fill_fraction=0.6234,
        sharpness=sharpness,
    )


def test_payload_shape_and_gate() -> None:
    sharp = coverage_metrics_payload("cam_0", _detection(SHARPNESS_MIN + 10))
    assert sharp["type"] == "coverage_metrics"
    assert sharp["camera"] == "cam_0"
    assert sharp["phase"] == "intrinsic"
    assert sharp["grid_count"] == 42
    assert sharp["fill_fraction"] == 0.6234
    assert sharp["sharpness_ok"] is True

    blurry = coverage_metrics_payload("cam_1", _detection(SHARPNESS_MIN - 10))
    assert blurry["sharpness_ok"] is False


def test_payload_when_not_found() -> None:
    payload = coverage_metrics_payload("cam_0", BoardDetection.empty())
    assert payload["board_found"] is False
    assert payload["grid_count"] == 0
    assert payload["sharpness_ok"] is False
