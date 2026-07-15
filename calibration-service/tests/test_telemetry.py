"""Telemetry payload tests."""

from __future__ import annotations

import numpy as np

from calibration_service.detection import BoardDetection
from calibration_service.telemetry import SharpnessBaseline, coverage_metrics_payload


def _detection(sharpness: float) -> BoardDetection:
    corners = np.zeros((42, 2), np.float32)
    return BoardDetection(
        found=True,
        corners=corners,
        ids=np.arange(42, dtype=np.int32),
        outline=np.zeros((4, 2), np.float32),
        board_coverage=0.6234,
        sharpness=sharpness,
        tilt_deg=18.0,
    )


def test_payload_shape_carries_the_relative_gauge() -> None:
    # The caller owns the SharpnessBaseline and passes the resolved flag.
    sharp = coverage_metrics_payload("cam_0", _detection(500.0), sharpness_ok=True)
    assert sharp["type"] == "coverage_metrics"
    assert sharp["camera"] == "cam_0"
    assert sharp["phase"] == "intrinsic"
    assert sharp["grid_count"] == 42
    assert sharp["board_coverage"] == 0.6234
    assert sharp["sharpness"] == 500.0
    assert sharp["sharpness_ok"] is True

    blurry = coverage_metrics_payload("cam_1", _detection(10.0), sharpness_ok=False)
    assert blurry["sharpness_ok"] is False


def test_payload_when_not_found() -> None:
    payload = coverage_metrics_payload("cam_0", BoardDetection.empty())
    assert payload["board_found"] is False
    assert payload["grid_count"] == 0
    assert payload["sharpness_ok"] is False


def test_sharpness_baseline_is_optimistic_during_warmup() -> None:
    # Too little history to judge -> never nag the operator early.
    baseline = SharpnessBaseline()
    assert all(baseline.ok(5.0) for _ in range(10))


def test_sharpness_baseline_flags_relative_to_the_recent_window() -> None:
    baseline = SharpnessBaseline()
    for _ in range(30):
        baseline.ok(100.0)  # establish a ~100 baseline (p90 ~ 100)
    assert baseline.ok(80.0) is True  # >= 0.5 * p90
    assert baseline.ok(20.0) is False  # a sudden blur (fast move) < 0.5 * p90
