"""Intrinsic calibration from ChArUco detections (ADR-0009, [[intrinsic-calibration-flow]]).

Two stages, kept separate so keyframe selection is testable without the OpenCV
solver:

- ``select_keyframes`` — from all detections of a camera, apply a stride (cheap
  temporal pre-filter for high-fps capture), drop blurry frames (sharpness gate,
  ADR-0008) and pick a diverse, capped subset (farthest-point sampling over
  tilt + image-position) so coverage/diversity is maximised, not the frame count.
- ``calibrate_intrinsic`` — run ``cv2.calibrateCameraExtended`` on the retained
  views (rational model, intrinsic guess, fixed aspect ratio — Caliscope flags),
  exposing ``perViewErrors`` for outlier rejection.

Modern OpenCV (>= 4.7) removed ``calibrateCameraCharuco``; the path is
``board.matchImagePoints`` (ChArUco corners → object/image points) then
``cv2.calibrateCameraExtended``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.board.dictionaries import resolve
from calibration_service.detection import BoardDetection, BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.telemetry import SHARPNESS_MIN

# Caliscope intrinsic flags: seed with a guess, rational (8-coef) distortion, fix aspect.
_CALIB_FLAGS = (
    cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_RATIONAL_MODEL | cv2.CALIB_FIX_ASPECT_RATIO
)
_DEFAULT_CAP = 25  # keyframes kept for the solve (MATLAB recommends 10-20)
_MIN_VIEWS = 6  # calib.io: at least ~6 observations
# cv2.calibrateCameraExtended's internal per-view pose init (cvFindExtrinsicCameraParams2)
# requires >= 6 point correspondences and hard-crashes below that — observed in
# Docker: "DLT algorithm needs at least 6 points... 'count' is 4". A near-edge-of-
# frame detection with only a handful of corners is a poor observation anyway, so
# this is filtered independently of BoardDetector's lower live-detection floor
# (detection/detector.py's _MIN_CORNERS=4, which only gates UI "board found").
_MIN_CORNERS_FOR_CALIBRATION = 6


@dataclass(frozen=True)
class IntrinsicResult:
    """Outcome of an intrinsic calibration (camera-array-config fields)."""

    matrix: list[list[float]]  # 3x3 K
    distortions: list[float]  # rational model coefficients
    error: float  # RMS reprojection error (px)
    per_view_errors: list[float]  # per-keyframe reprojection error
    grid_count: int  # total corners used across keyframes
    view_count: int  # keyframes used
    image_size: tuple[int, int]  # (width, height)


def _centroid(corners: NDArray[np.float32]) -> tuple[float, float]:
    c = corners.mean(axis=0)
    return float(c[0]), float(c[1])


def _is_well_spread(corners: NDArray[np.float32]) -> bool:
    """Reject near-collinear corner sets (2D rank-deficient).

    Point count alone doesn't guarantee a usable view: OpenCV's own planarity
    test inside calibrateCamera's per-view pose init can misclassify a thin,
    near-collinear sliver of corners (e.g. a single board row/edge — exactly the
    kind of "extreme" view farthest-point sampling favours) as non-planar,
    routing it into a DLT solve that hard-crashes below 6 points regardless of
    the guessed count. A cheap SVD rank check catches this before it reaches
    the solver (Caliscope does not do this; grounded in OpenCV's own
    calibration_base.cpp planarity-test mechanics).
    """
    centered = corners.astype(np.float64) - corners.mean(axis=0)
    singular_values = np.linalg.svd(centered, compute_uv=False)
    return bool(singular_values[1] > 0.02 * singular_values[0])


def select_keyframes(
    detections: list[BoardDetection],
    image_size: tuple[int, int],
    *,
    cap: int = _DEFAULT_CAP,
    stride: int = 1,
    sharpness_min: float = SHARPNESS_MIN,
) -> list[BoardDetection]:
    """Pick a diverse, capped, in-focus subset of detections for the solve.

    Stride pre-filters high-fps runs; the sharpness gate drops blurry frames; a
    minimum corner count + spread check drops views too sparse/degenerate for the
    solver (Caliscope filters the same way, before diversifying — see
    _MIN_CORNERS_FOR_CALIBRATION); then farthest-point sampling over
    (tilt, image-x, image-y) spreads the selection over orientations and sensor
    regions (coverage/diversity, [[coverage-metrics]]).
    """
    width, height = image_size
    candidates = [
        d
        for d in detections[:: max(1, stride)]
        if d.found
        and d.ids is not None
        and d.corners is not None
        and d.sharpness >= sharpness_min
        and d.count >= _MIN_CORNERS_FOR_CALIBRATION
        and _is_well_spread(d.corners)
    ]
    if len(candidates) <= cap:
        return candidates

    # Feature per candidate: tilt (normalised to ~[0,1] over 0-45 deg) + centroid position.
    features: list[tuple[float, float, float]] = []
    for d in candidates:
        cx, cy = _centroid(d.corners)  # type: ignore[arg-type]
        tilt = (d.tilt_deg or 0.0) / 45.0
        features.append((tilt, cx / width, cy / height))
    feats = np.asarray(features, dtype=np.float64)

    # Farthest-point sampling: start from the highest-corner-count frame, then
    # repeatedly add the candidate farthest from the already-selected set.
    start = int(max(range(len(candidates)), key=lambda i: candidates[i].count))
    selected = [start]
    min_dist = np.linalg.norm(feats - feats[start], axis=1)
    while len(selected) < cap:
        nxt = int(np.argmax(min_dist))
        selected.append(nxt)
        min_dist = np.minimum(min_dist, np.linalg.norm(feats - feats[nxt], axis=1))
    return [candidates[i] for i in selected]


def _cv_charuco_board(board: CalibrationBoard) -> cv2.aruco.CharucoBoard:
    return cv2.aruco.CharucoBoard(
        (board.columns, board.rows), 1.0, board.marker_ratio, resolve(board.dictionary)
    )


def _initial_camera_matrix(width: int, height: int) -> NDArray[np.float64]:
    f = float(width)
    return np.array([[f, 0.0, width / 2], [0.0, f, height / 2], [0.0, 0.0, 1.0]], np.float64)


def calibrate_intrinsic(
    detections: list[BoardDetection],
    board: CalibrationBoard,
    image_size: tuple[int, int],
) -> IntrinsicResult:
    """Calibrate camera intrinsics from retained ChArUco detections.

    Raises ``ValueError`` for a non-ChArUco board or too few usable views.
    """
    if board.board_type is not BoardType.CHARUCO:
        raise ValueError("intrinsic calibration requires a ChArUco board")

    cv_board = _cv_charuco_board(board)
    object_points: list[NDArray[np.float32]] = []
    image_points: list[NDArray[np.float32]] = []
    grid_count = 0
    for det in detections:
        if det.corners is None or det.ids is None or det.count < _MIN_CORNERS_FOR_CALIBRATION:
            continue
        if not _is_well_spread(det.corners):
            continue
        corners = det.corners.reshape(-1, 1, 2).astype(np.float32)
        ids = det.ids.reshape(-1, 1).astype(np.int32)
        obj, img = cv_board.matchImagePoints(corners, ids)  # type: ignore[call-overload]
        if obj is None or img is None or len(obj) < _MIN_CORNERS_FOR_CALIBRATION:
            continue
        object_points.append(obj)
        image_points.append(img)
        grid_count += int(obj.shape[0])

    if len(object_points) < _MIN_VIEWS:
        raise ValueError(f"need >= {_MIN_VIEWS} usable views, got {len(object_points)}")

    width, height = image_size
    guess = _initial_camera_matrix(width, height)
    # distCoeffs=None is valid at runtime (OpenCV allocates it); the cv2 stub types
    # it as required, hence the ignore.
    result = cv2.calibrateCameraExtended(  # type: ignore[call-overload]
        object_points, image_points, (width, height), guess, None, flags=_CALIB_FLAGS
    )
    rms, matrix, dist, _rvecs, _tvecs, _sdi, _sde, per_view = result
    return IntrinsicResult(
        matrix=np.asarray(matrix, float).tolist(),
        distortions=np.asarray(dist, float).ravel().tolist(),
        error=float(rms),
        per_view_errors=np.asarray(per_view, float).ravel().tolist(),
        grid_count=grid_count,
        view_count=len(object_points),
        image_size=(width, height),
    )


def compute_intrinsic_from_video(
    video_path: Path,
    board: CalibrationBoard,
    *,
    cap: int = _DEFAULT_CAP,
    stride: int = 1,
) -> IntrinsicResult:
    """Recompute intrinsics from a recorded capture (ADR-0019 record → compute).

    Reads every frame, detects the board, selects a diverse keyframe subset, then
    calibrates. Raises ``ValueError`` on an empty/unusable video (before the solver).
    """
    detector = BoardDetector(board)
    capture = cv2.VideoCapture(str(video_path))
    detections: list[BoardDetection] = []
    image_size: tuple[int, int] | None = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if image_size is None:
                image_size = (frame.shape[1], frame.shape[0])
            detections.append(detector.detect(cast("NDArray[np.uint8]", frame)))
    finally:
        capture.release()

    if image_size is None:
        raise ValueError(f"no readable frames in {video_path}")
    keyframes = select_keyframes(detections, image_size, cap=cap, stride=stride)
    return calibrate_intrinsic(keyframes, board, image_size)
