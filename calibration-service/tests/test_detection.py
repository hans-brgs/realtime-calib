"""Detection tests — closed loop with the board renderer (render -> detect)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.board import render_board_png
from calibration_service.board.dictionaries import resolve
from calibration_service.board.render import (
    _ARUCO_MARKER_PX,
    _ARUCO_QUIET_RATIO,
    _MARGIN_RATIO,
    PX_PER_SQUARE,
)
from calibration_service.detection import BoardDetector, guessed_camera_matrix
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


def test_guessed_camera_matrix_is_caliscopes_seed() -> None:
    # Caliscope v0.11.5's seed (core/calibrate_intrinsics.py, ddda95b4; ADR-0053): the
    # longer side as focal, the principal point at the pixel-centre image middle.
    np.testing.assert_array_equal(
        guessed_camera_matrix(1920, 1080),
        [[1920.0, 0.0, 959.5], [0.0, 1920.0, 539.5], [0.0, 0.0, 1.0]],
    )
    np.testing.assert_array_equal(
        guessed_camera_matrix(1080, 1920),
        [[1920.0, 0.0, 539.5], [0.0, 1920.0, 959.5], [0.0, 0.0, 1.0]],
    )


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


def _warped_marker(
    offset: tuple[float, float], angle_deg: float = 0.0, size: int = 900
) -> tuple[NDArray[np.uint8], NDArray[np.float64]]:
    """Render a marker into a larger canvas at a known sub-pixel offset and angle.

    Returns the image and the ground-truth corner positions (TL, TR, BR, BL), in
    OpenCV's pixel-centre coordinates, so a detector's corners can be scored
    against them. The shift + rotation come from a warpAffine with bilinear
    interpolation — the same partial-coverage edge pixels a real camera produces.
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
    centre = ((size - 1) / 2.0, (size - 1) / 2.0)  # pixel-centre coordinates
    matrix = np.asarray(cv2.getRotationMatrix2D(centre, angle_deg, 1.0), np.float64)
    matrix[:, 2] += np.asarray(offset, np.float64)
    shifted = cv2.warpAffine(
        canvas, matrix, (size, size), flags=cv2.INTER_LINEAR, borderValue=255
    )
    # The black border's outer edge sits at the tile's quiet-zone width (render
    # constants), in pixel-EDGE coordinates; INTER_AREA scales edge coordinates
    # exactly, and pixel-centre coordinates are edge coordinates - 0.5.
    tile_side = gray.shape[1]
    quiet = round(_ARUCO_MARKER_PX * _ARUCO_QUIET_RATIO)
    scale = side / tile_side
    low = base + quiet * scale - 0.5
    high = base + (quiet + _ARUCO_MARKER_PX) * scale - 0.5
    truth = np.array([[low, low], [high, low], [high, high], [low, high]], np.float64)
    return shifted.astype(np.uint8), truth @ matrix[:, :2].T + matrix[:, 2]


def _corner_scatter(params: cv2.aruco.DetectorParameters) -> float:
    """Corner spread (px) about the method's own mean inward bias, over sub-pixel shifts.

    The single-marker corners of every contour-based method sit ~0.5-0.8 px
    INSIDE the true edge (contour pixels are the outermost dark pixels, whose
    centres lie within the edge) — a systematic offset, not jitter. This isolates
    the jitter, the quantity ADR-0043 measured on real footage.
    """
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100), params
    )
    inward: list[float] = []
    tangential: list[float] = []
    # Slightly rotated views, as a real board always is: on a perfectly axis-
    # aligned edge every contour pixel shares one integer coordinate, and any
    # contour-based fit degenerates into whole-pixel steps.
    views = [((0.0, 0.0), 7.0), ((0.25, 0.5), -11.0), ((0.5, 0.25), 13.0), ((0.75, 0.75), -4.0)]
    for offset, angle in views:
        image, truth = _warped_marker(offset, angle)
        corners, ids, _ = detector.detectMarkers(image)
        assert ids is not None and 8 in ids.ravel().tolist()
        found = corners[int(np.flatnonzero(ids.ravel() == 8)[0])].reshape(4, 2)
        toward_centre = truth.mean(axis=0) - truth
        toward_centre /= np.linalg.norm(toward_centre, axis=1, keepdims=True)
        error = found.astype(np.float64) - truth
        along = (error * toward_centre).sum(axis=1)
        inward.extend(along.tolist())
        tangential.extend(np.linalg.norm(error - along[:, None] * toward_centre, axis=1))
    return float(np.sqrt(np.var(inward) + np.mean(np.square(tangential))))


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


def test_contour_refinement_cuts_corner_jitter_on_subpixel_offsets() -> None:
    # The measurable claim behind ADR-0043, on synthetic ground truth: edge-line
    # fitting places corners far more REPEATABLY than the polygon vertices of a
    # binarised contour (measured ~0.04 vs ~0.45 px). Jitter only — the shared
    # inward offset is removed by _corner_scatter.
    plain = _corner_scatter(_detector_params())
    contour = _corner_scatter(_detector_params(single_marker=True))
    assert contour < 0.5 * plain


_FIXTURES = Path(__file__).parent / "fixtures"


def _contour_assert_patch() -> NDArray[np.uint8]:
    """A ~7 px false-positive marker (id 17) that makes OpenCV 4.13's CONTOUR
    refinement assert (``nContours.size() >= 2``).

    Provenance: session calib-07-13-2026, cam_2, frame 601 (a frame without the
    target), cropped with a 16 px margin around the false positive (38x39 px).
    """
    patch = cv2.imread(str(_FIXTURES / "aruco_contour_assert.png"), cv2.IMREAD_GRAYSCALE)
    assert patch is not None
    return cast("NDArray[np.uint8]", patch)


def test_contour_refinement_failure_keeps_the_target_refined(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The real-session crash (cam_0, frame 790): the TARGET was fine, a false
    # positive elsewhere tripped OpenCV. The fallback refines the target alone on
    # a crop — the frame keeps its observation, at full CONTOUR precision.
    marker_board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=1, rows=1, marker_id=8
    )
    marker, _ = _warped_marker((0.25, 0.5))
    # Room for the false positive OUTSIDE the target's crop (0.75x its side).
    clean = np.pad(marker, ((0, 700), (0, 700)), constant_values=255)
    polluted = clean.copy()
    patch = _contour_assert_patch()
    polluted[1300 : 1300 + patch.shape[0], 1300 : 1300 + patch.shape[1]] = patch

    reference = BoardDetector(marker_board).detect(clean)
    with caplog.at_level(logging.WARNING, logger="calibration_service.detection.detector"):
        recovered = BoardDetector(marker_board).detect(polluted)

    # The fixture must still trip OpenCV, or this test no longer covers the fallback.
    assert any("CONTOUR refinement raised" in r.getMessage() for r in caplog.records)
    assert reference.found and recovered.found
    assert recovered.corners is not None and reference.corners is not None
    assert float(np.abs(recovered.corners - reference.corners).max()) < 0.05


def test_contour_refinement_failure_without_target_is_no_detection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker_board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=1, rows=1, marker_id=8
    )
    # The provenance frame itself (cam_2, frame 601) had no target: nothing to keep.
    canvas = np.full((200, 200), 255, np.uint8)
    patch = _contour_assert_patch()
    canvas[50 : 50 + patch.shape[0], 50 : 50 + patch.shape[1]] = patch
    with caplog.at_level(logging.WARNING, logger="calibration_service.detection.detector"):
        found = BoardDetector(marker_board).detect(canvas).found
    assert any("CONTOUR refinement raised" in r.getMessage() for r in caplog.records)
    assert not found


def test_charuco_view_dropped_when_subpixel_refinement_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # detectBoard's raw corners carry OpenCV 4.13's ~+0.5 px offset: an unrefined
    # view would be a biased observation, so a failed refinement drops it.
    def _raise(*_args: object) -> None:
        raise cv2.error("forced")

    board = _charuco()
    image = _decode(render_board_png(board))
    monkeypatch.setattr(cv2, "cornerSubPix", _raise)
    assert not BoardDetector(board).detect(image).found


def test_charuco_corners_match_pixel_centre_ground_truth() -> None:
    # Guards the load-bearing cornerSubPix pass: on OpenCV 4.13.0 detectBoard alone
    # is off by ~+0.5 px (opencv#25539), which would bias the principal point.
    board = _charuco(dictionary="DICT_4X4_100")
    margin = round(PX_PER_SQUARE * _MARGIN_RATIO)  # render_board_png's quiet zone
    image = _decode(render_board_png(board))
    det = BoardDetector(board).detect(image)
    assert det.found and det.corners is not None and det.ids is not None
    cv_board = cv2.aruco.CharucoBoard(
        (board.columns, board.rows), 1.0, board.marker_ratio, resolve(board.dictionary)
    )
    grid = np.asarray(cv_board.getChessboardCorners(), np.float64)[det.ids][:, :2]
    truth = margin + grid * PX_PER_SQUARE - 0.5  # square edges, pixel-centre coords
    offset = det.corners.astype(np.float64) - truth
    assert np.all(np.abs(offset.mean(axis=0)) < 0.1)


@pytest.mark.parametrize("board_type", [BoardType.CHARUCO, BoardType.ARUCO])
def test_inverted_board_is_detected(board_type: BoardType) -> None:
    # render_board_png inverts the print; detection must undo it (spec
    # calibration-board) — it used to find nothing at all on an inverted target.
    board = CalibrationBoard(
        board_type=board_type,
        dictionary="DICT_5X5_100",
        columns=7,
        rows=8,
        marker_id=7,
        inverted=True,
    )
    det = BoardDetector(board).detect(_decode(render_board_png(board)))
    assert det.found
    assert det.count == ((7 - 1) * (8 - 1) if board_type is BoardType.CHARUCO else 4)


def test_board_detection_compares_by_identity() -> None:
    # ndarray fields: a generated __eq__ compared two FOUND detections array by
    # array and raised ("truth value of an array is ambiguous") on ==, `in` and
    # list.index.
    detector = BoardDetector(_charuco())
    image = _decode(render_board_png(_charuco()))
    first, second = detector.detect(image), detector.detect(image)
    assert first.found and second.found
    assert first != second
    assert second not in [first]
    assert [first, second].index(second) == 1
