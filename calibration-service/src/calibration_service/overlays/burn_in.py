"""Burn-in overlays for the preview stream (ADR-0003, [[coverage-metrics]] (a)).

Draws the detected board polygon — coloured by instantaneous ``fill_fraction`` (a
distance proxy) — plus the corner dots, on a **preview-resolution copy** of the
frame. Detection runs at calibration resolution; corners are scaled down by
``resize_factor`` before drawing so overlays match the published preview (ADR-0015).
"""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.detection import BoardDetection

# Instantaneous fill → colour (BGR), from the coverage-metrics distance bands.
_RED = (40, 40, 255)  # ~#ff2828 — board too far (saturated; the gauge red is softer)
_ORANGE = (60, 146, 251)  # #fb923c — still far
_YELLOW = (36, 191, 251)  # #fbbf24 — acceptable
_GREEN = (153, 211, 52)  # #34d399 — good distance

_FILL_ALPHA = 0.22  # translucency of the filled polygon


def fill_color(coverage: float) -> tuple[int, int, int]:
    """Map board coverage (extrapolated board area / frame) to a burn-in colour (BGR).

    Green at >= 0.50 = the calib.io criterion ("at least half the image area,
    fronto-parallel"). See BoardDetection.board_coverage.
    """
    if coverage < 0.15:
        return _RED
    if coverage < 0.30:
        return _ORANGE
    if coverage < 0.50:
        return _YELLOW
    return _GREEN


def draw_overlay(
    image: NDArray[np.uint8],
    detection: BoardDetection,
    resize_factor: float = 1.0,
) -> NDArray[np.uint8]:
    """Return a preview-resolution BGR copy of ``image`` with the board burn-in.

    ``image`` is the native-resolution BGR capture frame; ``detection`` holds the
    corners at that same resolution. The result is never the input array (safe to
    publish while the original feeds detection/recording).
    """
    preview: NDArray[np.uint8]
    if resize_factor != 1.0:
        scaled = cv2.resize(
            image, None, fx=resize_factor, fy=resize_factor, interpolation=cv2.INTER_AREA
        )
        preview = cast("NDArray[np.uint8]", scaled)
    else:
        preview = image.copy()
    if preview.ndim == 2:
        preview = cast("NDArray[np.uint8]", cv2.cvtColor(preview, cv2.COLOR_GRAY2BGR))

    if not detection.found or detection.corners is None:
        return preview

    color = fill_color(detection.board_coverage)

    # Outline = the extrapolated physical board contour (falls back to the corner hull).
    if detection.outline is not None:
        outline = np.round(detection.outline * resize_factor).astype(np.int32)
    else:
        hull = cv2.convexHull(np.round(detection.corners * resize_factor).astype(np.int32))
        outline = cast("NDArray[np.int32]", hull).reshape(-1, 2)

    filled = preview.copy()
    cv2.fillPoly(filled, [outline], color)
    cv2.addWeighted(filled, _FILL_ALPHA, preview, 1.0 - _FILL_ALPHA, 0, dst=preview)
    cv2.polylines(preview, [outline], isClosed=True, color=color, thickness=2, lineType=cv2.LINE_AA)
    for x, y in np.round(detection.corners * resize_factor).astype(np.int32):
        cv2.circle(preview, (int(x), int(y)), 3, color, -1, cv2.LINE_AA)

    return preview
