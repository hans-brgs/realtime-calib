"""Coverage-metrics telemetry pushed to the webapp over the LiveKit data channel.

Only aggregates cross the wire ([[coverage-metrics]], [[board-observation]]) — never
raw observations. Phase 3.1 sends the per-frame indicators (fill, sharpness gate,
detected corner count); the cumulative gauges (coverage, orientation diversity) come
with the metrics slice.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from calibration_service.detection import BoardDetection
from calibration_service.synchronization import CovisibilityGraph

TELEMETRY_TOPIC = "telemetry"

# Relative sharpness gauge (ADR-0038). Laplacian-variance sharpness has no
# universal threshold (it shifts with resolution, lighting, lens, print
# contrast), so there is no absolute gate anymore — the live "sharp?" flag
# compares against what THIS camera recently produced.
_SHARPNESS_WINDOW = 90  # ~3 s of detections at the intrinsic 30 Hz grid
_SHARPNESS_WARMUP = 15  # samples before the gauge judges (else optimistic)
_SHARPNESS_OK_FRACTION = 0.5  # current must clear this share of the recent p90


class SharpnessBaseline:
    """Relative "is the board sharp right now?" gauge for the live overlay (ADR-0038).

    Instead of an absolute blur gate, ``ok()`` compares the current frame against
    what this camera produces when the board is held still: a rolling window of
    recent sharpness, the flag set when the current value clears a fraction of the
    window's p90 high-water mark. The operator reads it as a "you're moving too
    fast" coach, not a pass/fail. Native and preview detection differ in scale, so
    keep one instance per (camera, capture phase).
    """

    def __init__(self) -> None:
        self._recent: deque[float] = deque(maxlen=_SHARPNESS_WINDOW)

    def ok(self, sharpness: float) -> bool:
        """Record ``sharpness`` and return whether it clears the recent baseline."""
        self._recent.append(sharpness)
        if len(self._recent) < _SHARPNESS_WARMUP:
            return True  # warm-up: too little history to judge -> don't nag
        p90 = float(np.percentile(self._recent, 90))
        return sharpness >= _SHARPNESS_OK_FRACTION * p90


def coverage_metrics_payload(
    camera: str, detection: BoardDetection, phase: str = "intrinsic", *, sharpness_ok: bool = False
) -> dict[str, object]:
    """Build the ``coverage_metrics`` data-channel payload for one detection.

    ``sharpness_ok`` is the relative live gauge (:class:`SharpnessBaseline`),
    computed by the caller which owns the per-camera window.
    """
    return {
        "type": "coverage_metrics",
        "camera": camera,
        "phase": phase,
        "board_found": detection.found,
        "board_coverage": round(detection.board_coverage, 4),
        "tilt_deg": round(detection.tilt_deg, 1) if detection.tilt_deg is not None else None,
        "sharpness": round(detection.sharpness, 1),
        "sharpness_ok": sharpness_ok,
        "grid_count": detection.count,
    }


def covisibility_payload(graph: CovisibilityGraph) -> dict[str, object]:
    """Build the ``covisibility`` data-channel payload for the extrinsic gauges.

    Pair counts drive the webapp's co-visibility matrix ([[extrinsic-calibration-flow]]);
    ``board_frames`` gives each camera's own detection tally, ``synced_groups`` the
    denominator (groups meeting the quorum, board seen or not).
    """
    return {
        "type": "covisibility",
        "phase": "extrinsic",
        "cameras": list(graph.cameras),
        "pairs": [
            {"a": a, "b": b, "count": count}
            for (a, b), count in sorted(graph.pair_counts.items())
        ],
        "board_frames": dict(graph.board_frames),
        "synced_groups": graph.synced_groups,
    }
