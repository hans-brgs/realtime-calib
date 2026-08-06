"""Detection tests — closed loop with the board renderer (render -> detect)."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.board import render_board_png
from calibration_service.detection import BoardDetector
from calibration_service.detection.detector import _detector_params, _tilt_deg
from calibration_service.models.board import BoardType, CalibrationBoard


def _decode(png: bytes) -> NDArray[np.uint8]:
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert image is not None
    return cast("NDArray[np.uint8]", image)


def _charuco(**overrides: object) -> CalibrationBoard:
    params: dict[str, object] = {
        "board_type": BoardType.CHARUCO,
        "dictionary": "DICT_5X5_100",
        "columns": 7,
        "rows": 8,
    }
    params.update(overrides)
    return CalibrationBoard(**params)  # type: ignore[arg-type]


def test_detects_all_charuco_corners() -> None:
    board = _charuco()
    image = _decode(render_board_png(board))
    det = BoardDetector(board).detect(image)
    assert det.found
    # A C x R ChArUco board has (C-1) x (R-1) interior corners.
    assert det.count == (7 - 1) * (8 - 1)
    # Extrapolated board outline + coverage (board fills the rendered frame).
    assert det.outline is not None and det.outline.shape == (4, 2)
    assert 0.0 < det.board_coverage <= 1.0
    assert det.sharpness > 0.0
    # A rendered board is fronto-parallel → tilt near 0.
    assert det.tilt_deg is not None and det.tilt_deg < 5.0


def test_tilt_none_for_collinear_points() -> None:
    # 4 corners on the same board row → collinear → pose ill-defined (no crash).
    obj = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]], np.float32)
    img = np.array([[0, 0], [10, 0], [20, 0], [30, 0]], np.float32)
    assert _tilt_deg(obj, img, 640, 480) is None


def test_tilt_frontal_square_near_zero() -> None:
    # Axis-aligned square (4 non-collinear coplanar points) → frontal → ~0 deg, no crash.
    obj = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    img = np.array([[300, 220], [340, 220], [340, 260], [300, 260]], np.float32)
    tilt = _tilt_deg(obj, img, 640, 480)
    assert tilt is not None and tilt < 10.0


def test_tilt_ippe_square_path() -> None:
    # Single-marker path: canonical centered square + IPPE_SQUARE, frontal → ~0 deg.
    obj = np.array([[-0.5, 0.5, 0], [0.5, 0.5, 0], [0.5, -0.5, 0], [-0.5, -0.5, 0]], np.float32)
    img = np.array([[300, 220], [340, 220], [340, 260], [300, 260]], np.float32)
    tilt = _tilt_deg(obj, img, 640, 480, square=True)
    assert tilt is not None and tilt < 10.0


def test_blank_frame_not_found() -> None:
    board = _charuco()
    blank = np.full((480, 640), 255, np.uint8)
    det = BoardDetector(board).detect(blank)
    assert not det.found
    assert det.count == 0


def test_detects_single_aruco_marker() -> None:
    board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=7
    )
    image = _decode(render_board_png(board))
    det = BoardDetector(board).detect(image)
    assert det.found
    assert det.count == 4  # a single marker contributes its 4 corners
    assert det.ids is not None and set(det.ids.tolist()) == {7}


def test_wrong_marker_id_not_found() -> None:
    rendered = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=7
    )
    image = _decode(render_board_png(rendered))
    looking_for = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=42
    )
    det = BoardDetector(looking_for).detect(image)
    assert not det.found


def _warped_marker(offset: tuple[float, float], size: int = 900) -> tuple[
    NDArray[np.uint8], NDArray[np.float64]
]:
    """Render a marker into a larger canvas at a known sub-pixel offset.

    Returns the image and the ground-truth corner positions (TL, TR, BR, BL) so
    a detector's corners can be scored against them. The sub-pixel shift comes
    from a warpAffine with bilinear interpolation — the same partial-coverage
    edge pixels a real camera produces.
    """
    board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=1, rows=1, marker_id=8
    )
    tile = _decode(render_board_png(board))
    gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY) if tile.ndim == 3 else tile
    canvas = np.full((size, size), 255, np.uint8)
    side = size // 2
    scaled = cv2.resize(gray, (side, side), interpolation=cv2.INTER_AREA)
    base = size // 4
    canvas[base : base + side, base : base + side] = scaled
    matrix = np.array([[1.0, 0.0, offset[0]], [0.0, 1.0, offset[1]]], np.float64)
    shifted = cv2.warpAffine(
        canvas, matrix, (size, size), flags=cv2.INTER_LINEAR, borderValue=255
    )
    # The rendered tile carries a white quiet zone: the black marker border sits
    # one module inside the tile (a DICT_4X4 marker is 6 modules wide).
    module = side / 6.0
    low = base + module + offset[0], base + module + offset[1]
    high = base + side - module + offset[0], base + side - module + offset[1]
    truth = np.array(
        [[low[0], low[1]], [high[0], low[1]], [high[0], high[1]], [low[0], high[1]]],
        np.float64,
    )
    return shifted.astype(np.uint8), truth


def _corner_error(params: cv2.aruco.DetectorParameters, offset: tuple[float, float]) -> float:
    image, truth = _warped_marker(offset)
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100), params
    )
    corners, ids, _ = detector.detectMarkers(image)
    assert ids is not None and 8 in ids.ravel().tolist()
    found = corners[int(np.flatnonzero(ids.ravel() == 8)[0])].reshape(4, 2).astype(np.float64)
    return float(np.mean(np.linalg.norm(found - truth, axis=1)))


def test_single_marker_path_uses_contour_refinement() -> None:
    # ADR-0043: refinement splits by path — CONTOUR where raw marker corners are
    # the calibration observations, NONE on the ChArUco path (OpenCV warns it
    # degrades chessboard interpolation).
    assert (
        _detector_params(single_marker=True).cornerRefinementMethod
        == cv2.aruco.CORNER_REFINE_CONTOUR
    )
    assert _detector_params().cornerRefinementMethod == cv2.aruco.CORNER_REFINE_NONE

    marker_board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=1, rows=1, marker_id=8
    )
    charuco_board = _charuco(dictionary="DICT_4X4_100")
    assert (
        BoardDetector(marker_board)._aruco.getDetectorParameters().cornerRefinementMethod
        == cv2.aruco.CORNER_REFINE_CONTOUR
    )
    assert (
        BoardDetector(charuco_board)._charuco.getDetectorParameters().cornerRefinementMethod
        == cv2.aruco.CORNER_REFINE_NONE
    )


def test_contour_refinement_beats_no_refinement_on_subpixel_offsets() -> None:
    # The measurable claim behind ADR-0043, on synthetic ground truth: edge-line
    # fitting locates corners better than the polygon vertices of a binarised
    # contour. Averaged over sub-pixel shifts, where the two differ most.
    offsets = [(0.0, 0.0), (0.25, 0.5), (0.5, 0.25), (0.75, 0.75), (0.5, 0.5)]
    plain = float(np.mean([_corner_error(_detector_params(), o) for o in offsets]))
    contour = float(
        np.mean([_corner_error(_detector_params(single_marker=True), o) for o in offsets])
    )
    assert contour < plain
