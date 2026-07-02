"""Coverage-metrics telemetry pushed to the webapp over the LiveKit data channel.

Only aggregates cross the wire ([[coverage-metrics]], [[board-observation]]) — never
raw observations. Phase 3.1 sends the per-frame indicators (fill, sharpness gate,
detected corner count); the cumulative gauges (coverage, orientation diversity) come
with the metrics slice.
"""

from __future__ import annotations

from calibration_service.detection import BoardDetection

TELEMETRY_TOPIC = "telemetry"

# Blur gate: a keyframe below this Laplacian-variance is rejected ([[coverage-metrics]]
# (d)). Heuristic default — meant to become a configurable setting later.
SHARPNESS_MIN = 60.0


def coverage_metrics_payload(
    camera: str, detection: BoardDetection, phase: str = "intrinsic"
) -> dict[str, object]:
    """Build the ``coverage_metrics`` data-channel payload for one detection."""
    return {
        "type": "coverage_metrics",
        "camera": camera,
        "phase": phase,
        "board_found": detection.found,
        "board_coverage": round(detection.board_coverage, 4),
        "tilt_deg": round(detection.tilt_deg, 1) if detection.tilt_deg is not None else None,
        "sharpness": round(detection.sharpness, 1),
        "sharpness_ok": bool(detection.sharpness >= SHARPNESS_MIN),
        "grid_count": detection.count,
    }
