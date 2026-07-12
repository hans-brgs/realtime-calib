"""Preview-draw path of the camera publisher (ADR-0003 / ADR-0015).

Two guarantees are pinned here:

1. **LiveKit size contract** — every frame handed to ``push`` must equal the
   published preview size, with EVEN dimensions (VP8 / H.264 4:2:0). The draw path
   must return exactly ``_preview_size(...)`` for any native geometry, including
   ones whose scaled height would otherwise land odd (e.g. 1918x1080 -> 540, not
   541).
2. **Drawn AT preview resolution** — the burn-in is composited on the already
   downscaled frame (``draw_overlay`` given the explicit target size), NOT at native
   resolution then downscaled. A spy pins this so a regression back to the costly
   native-then-downscale path is caught.
"""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.board import render_board_png
from calibration_service.detection import BoardDetection, BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.overlays import draw_overlay
from calibration_service.transport import camera_publish_service as cps
from calibration_service.transport.camera_publish_service import (
    _draw_preview,
    _preview_size,
    _process_frame,
)


def _charuco() -> CalibrationBoard:
    return CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )


def _synthetic_detection(width: int, height: int) -> BoardDetection:
    """A found detection with native-resolution corners/outline over most of the frame.

    Deterministic (no detector run) so size-mapping can be tested at arbitrary
    native geometries; it still exercises fillPoly/addWeighted/polylines/circle.
    """
    x0, y0, x1, y1 = width * 0.2, height * 0.2, width * 0.8, height * 0.8
    outline = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)
    corners = np.array(
        [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [(x0 + x1) / 2, (y0 + y1) / 2]], np.float32
    )
    ids = np.arange(corners.shape[0], dtype=np.int32)
    return BoardDetection(
        found=True,
        corners=corners,
        ids=ids,
        outline=outline,
        board_coverage=0.6,
        sharpness=100.0,
        tilt_deg=0.0,
    )


@pytest.mark.parametrize(
    "native",
    [(1920, 1080), (1918, 1080), (1280, 720), (640, 480), (641, 480)],
)
def test_draw_preview_returns_exact_even_preview_size(native: tuple[int, int]) -> None:
    width, height = native
    image: NDArray[np.uint8] = np.full((height, width, 3), 40, np.uint8)
    preview_size = _preview_size(width, height)

    out = _draw_preview(image, _synthetic_detection(width, height), preview_size)

    assert (out.shape[1], out.shape[0]) == preview_size  # exact published size
    assert out.shape[1] % 2 == 0 and out.shape[0] % 2 == 0  # 4:2:0 needs even dims
    assert out.ndim == 3 and out.dtype == np.uint8
    assert out.base is not image  # distinct buffer, safe to publish while the original detects


def test_draw_preview_1918_would_be_odd_without_exact_size() -> None:
    # 1918 wide: a scalar-factor resize (fx=fy=960/1918) yields height 541 (odd);
    # the exact-size path must still return 540 so the track stays valid.
    assert _preview_size(1918, 1080) == (960, 540)
    image: NDArray[np.uint8] = np.full((1080, 1918, 3), 40, np.uint8)

    out = _draw_preview(image, _synthetic_detection(1918, 1080), (960, 540))

    assert (out.shape[1], out.shape[0]) == (960, 540)


def test_draw_preview_none_detection_is_plain_downscale() -> None:
    image: NDArray[np.uint8] = np.full((1080, 1920, 3), 40, np.uint8)
    preview_size = _preview_size(1920, 1080)

    out = _draw_preview(image, None, preview_size)

    assert (out.shape[1], out.shape[0]) == preview_size
    expected = cv2.resize(image, preview_size, interpolation=cv2.INTER_AREA)
    assert np.array_equal(out, expected)  # no overlay: a straight area-downscale


def test_draw_preview_composites_at_preview_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay is drawn on the downscaled frame (draw_overlay given preview_size),
    not at native resolution then downscaled — this is the perf contract of the lot."""
    image: NDArray[np.uint8] = np.full((1080, 1920, 3), 40, np.uint8)
    preview_size = _preview_size(1920, 1080)
    real = draw_overlay  # the canonical symbol; cps re-imports it (spy patches cps)
    seen: list[tuple[int, int] | None] = []

    def spy(
        img: NDArray[np.uint8], det: BoardDetection, size: tuple[int, int] | None = None
    ) -> NDArray[np.uint8]:
        seen.append(size)
        return real(img, det, size)

    monkeypatch.setattr(cps, "draw_overlay", spy)

    _draw_preview(image, _synthetic_detection(1920, 1080), preview_size)

    assert seen == [preview_size]  # composited at preview res (ADR-0003), never native


def test_process_frame_detects_and_returns_preview_size() -> None:
    board = _charuco()
    decoded = cv2.imdecode(np.frombuffer(render_board_png(board), np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    png = cast("NDArray[np.uint8]", decoded)
    preview_size = _preview_size(png.shape[1], png.shape[0])

    preview, detection = _process_frame(
        BoardDetector(board), png, preview_size, detect_at_preview=False
    )

    assert detection.found  # real detection ran on the native frame
    assert (preview.shape[1], preview.shape[0]) == preview_size
    assert preview.ndim == 3 and preview.dtype == np.uint8


def test_process_frame_detect_at_preview_feeds_the_downscaled_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extrinsic sweep detects at preview res (16->6 ms): the detector must receive
    the downscaled frame, not the native one. Pinned by capturing the shape it sees."""
    detector = BoardDetector(_charuco())
    preview_size = _preview_size(1920, 1080)  # (960, 540)
    image: NDArray[np.uint8] = np.full((1080, 1920, 3), 40, np.uint8)
    seen: list[tuple[int, int]] = []

    def spy_detect(img: NDArray[np.uint8]) -> BoardDetection:
        seen.append((img.shape[1], img.shape[0]))
        return _synthetic_detection(img.shape[1], img.shape[0])

    monkeypatch.setattr(detector, "detect", spy_detect)

    preview, _ = _process_frame(detector, image, preview_size, detect_at_preview=True)
    assert seen == [preview_size]  # detector ran on the 960x540 frame, not (1920, 1080)
    assert (preview.shape[1], preview.shape[0]) == preview_size

    seen.clear()
    _process_frame(detector, image, preview_size, detect_at_preview=False)
    assert seen == [(1920, 1080)]  # intrinsic path still feeds the full-resolution frame


def test_draw_preview_detect_at_preview_redraws_preview_space_detection() -> None:
    """Redraw tick during the sweep: a NATIVE frame + a detection whose corners are
    already in preview space must still yield the exact preview size — the frame is
    downscaled first and the corners drawn at scale 1 (no double-scaling)."""
    image: NDArray[np.uint8] = np.full((1080, 1920, 3), 40, np.uint8)
    preview_size = _preview_size(1920, 1080)  # (960, 540)
    preview_space_detection = _synthetic_detection(*preview_size)

    out = _draw_preview(image, preview_space_detection, preview_size, detect_at_preview=True)

    assert (out.shape[1], out.shape[0]) == preview_size
    assert out.ndim == 3 and out.dtype == np.uint8
