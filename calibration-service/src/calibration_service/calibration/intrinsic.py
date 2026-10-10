"""Intrinsic calibration from ChArUco detections (ADR-0009, [[intrinsic-calibration-flow]]).

Two stages, kept separate so keyframe selection is testable without the OpenCV
solver:

- ``select_keyframes`` — from all detections of a camera, pick a diverse, capped
  subset: farthest-point sampling over tilt + image-position places the keyframes
  (diversity), and the sharpest candidate of each cell is kept (quality within a
  cell, no absolute blur gate — ADR-0038). Coverage/diversity is maximised, not
  the frame count.
- ``calibrate_intrinsic`` — run ``cv2.calibrateCameraExtended`` on the retained
  views (classic 5-coefficient model — real Caliscope parity, ADR-0032; solved
  from Caliscope v0.11.5's seed, OpenCV's own initialisation standing in when
  that solve fails — ADR-0053), exposing
  ``perViewErrors`` for outlier rejection.

Modern OpenCV (>= 4.7) removed ``calibrateCameraCharuco``; the path is
``board.matchImagePoints`` (ChArUco corners → object/image points) then
``cv2.calibrateCameraExtended``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypedDict, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.board.dictionaries import resolve
from calibration_service.calibration.uncertainty import (
    ROBUST_COVERAGE,
    intrinsic_covariances,
    projection_uncertainty,
)
from calibration_service.detection import BoardDetection, BoardDetector, guessed_camera_matrix
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.resolution import output_size, to_output

# Real Caliscope parity (ADR-0032, verified against caliscope source): plain
# cv2.calibrateCamera with NO model flags — classic 5-coefficient distortion
# [k1,k2,p1,p2,k3], free aspect ratio. The former RATIONAL_MODEL+FIX_ASPECT anchor
# was not grounded in caliscope code and produced degenerate per-camera
# coefficients. The guess seed (Caliscope v0.11.5's) is solved next to OpenCV's own
# initialisation (the flag-free solve of Caliscope <= 0.5.4), the lower cost kept
# (ADR-0053).
_CALIB_FLAGS = cv2.CALIB_USE_INTRINSIC_GUESS
# stride/cap defaults live in calibration_service.tuning (ADR-0036); the transport
# layer resolves omitted request fields there and always passes explicit values.
_MIN_VIEWS = 6  # calib.io: at least ~6 observations
# cv2.calibrateCameraExtended's internal per-view pose init (cvFindExtrinsicCameraParams2)
# requires >= 6 point correspondences and hard-crashes below that — observed in
# Docker: "DLT algorithm needs at least 6 points... 'count' is 4". A near-edge-of-
# frame detection with only a handful of corners is a poor observation anyway, so
# this is filtered independently of BoardDetector's lower live-detection floor
# (detection/detector.py's _MIN_CORNERS=4, which only gates UI "board found").
_MIN_CORNERS_FOR_CALIBRATION = 6
logger = logging.getLogger(__name__)

# Anti-"sliver" gate: a view whose corners are near-collinear is rank-deficient
# (2nd singular value under this share of the 1st) and derails the solver's pose
# init — see _is_well_spread. Prudent and eprouve; not a tuning knob.
_MIN_SPREAD_RATIO = 0.02


@dataclass(frozen=True)
class IntrinsicResult:
    """Outcome of an intrinsic calibration (camera-array-config fields)."""

    matrix: list[list[float]]  # 3x3 K
    distortions: list[float]  # classic 5 coefficients [k1, k2, p1, p2, k3] (ADR-0032)
    error: float  # RMS reprojection error (px)
    per_view_errors: list[float]  # per-keyframe reprojection error
    grid_count: int  # total corners used across keyframes
    view_count: int  # keyframes used
    image_size: tuple[int, int]  # (width, height)
    # Review metrics (ADR-0022). All resolution-independent, so ``scaled()`` leaves them
    # unchanged. `coverage` = per-cell count of keyframes whose detected-corner hull
    # covers it (redundancy map, ADR-0039: 0 = never, 1 = fragile, 3+ = robust);
    # `image_coverage` = union-of-quads area fraction (no arbitrary grid);
    # `orientation_bins` = occupied 45deg tilt-azimuth sectors (Caliscope, /8);
    # `board_quads` = each keyframe board's 4 outline corners in 3D camera coords.
    coverage: tuple[tuple[int, ...], ...] = ()
    image_coverage: float = 0.0
    orientation_bins: int = 0
    board_quads: tuple[tuple[tuple[float, float, float], ...], ...] = ()
    # Sharpness (Laplacian variance) of the retained keyframes — the former
    # absolute gate is gone (ADR-0038), so these make "how sharp did we manage to
    # get?" observable: a uniformly-blurry sweep now succeeds instead of failing.
    sharpness_min: float = 0.0
    sharpness_median: float = 0.0
    # Projection uncertainty (ADR-0055): 1-sigma px (euclidean) per coverage cell, None
    # outside the lens model (past its distortion fold), and its RMS over the modelled
    # cells >= 3 keyframes cover and over those none does; `uncertainty_unmodelled` is
    # the share of cells outside the model. Empty / None when the covariance is
    # singular (degenerate views) or no cell is in that band.
    uncertainty: tuple[tuple[float | None, ...], ...] = ()
    uncertainty_covered_px: float | None = None
    uncertainty_uncovered_px: float | None = None
    uncertainty_unmodelled: float = 0.0

    def scaled(self, factor: float) -> IntrinsicResult:
        """Return the intrinsics at ``factor``x resolution (ADR-0015 resize).

        We calibrate at native resolution for accuracy then map K + image size
        to the operator's output resolution, pixel centres included (ADR-0051,
        ``calibration_service.resolution``); the normalised distortion coefficients are
        unchanged. Pixel errors and uncertainties scale by the nominal ``factor``.
        """
        if factor == 1.0:
            return self
        return replace(
            self,
            matrix=to_output(self.matrix, self.image_size, factor),
            error=self.error * factor,
            per_view_errors=[e * factor for e in self.per_view_errors],
            image_size=output_size(self.image_size, factor),
            uncertainty=tuple(
                tuple(None if v is None else round(v * factor, 3) for v in row)
                for row in self.uncertainty
            ),
            uncertainty_covered_px=_times(self.uncertainty_covered_px, factor),
            uncertainty_uncovered_px=_times(self.uncertainty_uncovered_px, factor),
        )


def _times(value: float | None, factor: float) -> float | None:
    return None if value is None else value * factor


# Accumulation-map width; rows derive from the image aspect for ~square cells.
# Small enough to ship in the metrics/telemetry payload (~96x54 at 16:9).
_COVERAGE_COLS = 96
# The union-area metric is read on a finer raster: the cell-centre test is unbiased,
# and at this width its scatter stays under 0.5 % even for a small quad.
_UNION_COLS = 384


def _hull_masks(
    image_points: list[NDArray[np.float32]], image_size: tuple[int, int], cols: int
) -> Iterator[NDArray[np.bool_]]:
    """Per keyframe, the cells whose CENTRE lies in its board's convex hull.

    A cell counts when its centre is inside, not when an edge touches it: the
    former truncate-and-fill credited every boundary cell and overestimated a
    quad's area by 8 to 28 % at 96 columns (audit INT-10); the centre test is
    unbiased.
    """
    width, height = image_size
    rows = max(1, round(cols * height / max(1, width)))
    xs = (np.arange(cols) + 0.5) * width / cols
    ys = (np.arange(rows) + 0.5) * height / rows
    grid_x, grid_y = np.meshgrid(xs, ys)
    for pts in image_points:
        hull = cv2.convexHull(pts.reshape(-1, 2).astype(np.float32)).reshape(-1, 2)
        hull = hull.astype(np.float64)
        nxt = np.roll(hull, -1, axis=0)
        orientation = np.sign(np.sum(hull[:, 0] * nxt[:, 1] - nxt[:, 0] * hull[:, 1]))
        inside = np.zeros((rows, cols), bool)
        # Only the cells around the hull's bounding box, a cell of margin each side:
        # the same mask, 25x cheaper than testing the whole grid.
        c0 = max(0, int(np.floor(hull[:, 0].min() * cols / width - 0.5)))
        c1 = min(cols, int(np.ceil(hull[:, 0].max() * cols / width - 0.5)) + 1)
        r0 = max(0, int(np.floor(hull[:, 1].min() * rows / height - 0.5)))
        r1 = min(rows, int(np.ceil(hull[:, 1].max() * rows / height - 0.5)) + 1)
        if orientation != 0 and c0 < c1 and r0 < r1:
            box_x, box_y = grid_x[r0:r1, c0:c1], grid_y[r0:r1, c0:c1]
            box = np.ones(box_x.shape, bool)
            for (x0, y0), (x1, y1) in zip(hull, nxt, strict=True):
                box &= orientation * ((x1 - x0) * (box_y - y0) - (y1 - y0) * (box_x - x0)) >= 0
            inside[r0:r1, c0:c1] = box
        yield inside


def _coverage_map(
    image_points: list[NDArray[np.float32]],
    image_size: tuple[int, int],
    cols: int = _COVERAGE_COLS,
) -> tuple[tuple[int, ...], ...]:
    """Per-cell keyframe-coverage count = quad accumulation map (ADR-0039).

    Points have AREA where corners have none: each keyframe's board is the convex
    hull of its DETECTED corners (a partial detection credits only what it saw),
    rasterised onto a ``cols``-wide grid (rows from the image aspect) and summed.
    The intensity is a redundancy map — 0 = never covered (go fill it), 1 = seen
    once (fragile), 3+ = well constrained.
    """
    width, height = image_size
    rows = max(1, round(cols * height / max(1, width)))
    acc = np.zeros((rows, cols), dtype=np.int32)
    for mask in _hull_masks(image_points, image_size, cols):
        acc += mask
    return tuple(tuple(int(v) for v in grid_row) for grid_row in acc)


_ORIENTATION_SECTORS = 8  # Caliscope 45deg tilt-azimuth bins
_FRONTAL_TILT_DEG = 8.0  # below this the tilt direction is meaningless -> not binned


def _union_coverage(
    image_points: list[NDArray[np.float32]], image_size: tuple[int, int], cols: int = _UNION_COLS
) -> float:
    """Image coverage = area fraction of the union of the keyframe quads (ADR-0039).

    Read by the unbiased cell-centre test on a raster fine enough that its value
    no longer depends on the grid (within 0.5 %, audit INT-10).
    """
    union: NDArray[np.bool_] | None = None
    for mask in _hull_masks(image_points, image_size, cols):
        union = mask if union is None else union | mask
    return 0.0 if union is None else float(union.mean())


def _orientation_bins(rvecs: list[NDArray[np.float64]]) -> int:
    """Occupied 45deg tilt-azimuth sectors (Caliscope orientation_count, out of 8).

    The board normal in camera coords is R's third column; frames too close to frontal
    (no meaningful tilt direction) are dropped, the rest binned by azimuth.
    """
    occupied: set[int] = set()
    for rvec in rvecs:
        rmat, _ = cv2.Rodrigues(rvec)
        normal = rmat[:, 2]
        tilt = np.degrees(np.arccos(min(1.0, abs(float(normal[2])))))
        if tilt < _FRONTAL_TILT_DEG:
            continue
        azimuth = np.arctan2(float(normal[1]), float(normal[0]))  # -pi..pi
        sector = int((azimuth + np.pi) / (2 * np.pi) * _ORIENTATION_SECTORS)
        occupied.add(sector % _ORIENTATION_SECTORS)
    return len(occupied)


def _board_quads(
    rvecs: list[NDArray[np.float64]],
    tvecs: list[NDArray[np.float64]],
    cv_board: cv2.aruco.CharucoBoard,
) -> tuple[tuple[tuple[float, float, float], ...], ...]:
    """Each keyframe board's 4 outline corners in 3D camera coords (pose scene, ADR-0022).

    Uses the board's chessboard-corner bounding rectangle (square units) transformed by
    the calibrated per-view pose; the camera sits at the origin in the scene.
    """
    chess = np.asarray(cv_board.getChessboardCorners(), np.float64).reshape(-1, 3)
    low, high = chess.min(axis=0), chess.max(axis=0)
    outline = np.array(
        [
            [low[0], low[1], 0.0],
            [high[0], low[1], 0.0],
            [high[0], high[1], 0.0],
            [low[0], high[1], 0.0],
        ]
    )
    quads: list[tuple[tuple[float, float, float], ...]] = []
    for rvec, tvec in zip(rvecs, tvecs, strict=True):
        rmat, _ = cv2.Rodrigues(rvec)
        corners = outline @ rmat.T + tvec.reshape(3)
        quads.append(tuple((float(p[0]), float(p[1]), float(p[2])) for p in corners))
    return tuple(quads)


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
    return bool(singular_values[1] > _MIN_SPREAD_RATIO * singular_values[0])


def select_keyframes(
    detections: list[BoardDetection],
    image_size: tuple[int, int],
    *,
    cap: int,
) -> list[BoardDetection]:
    """Pick a diverse, capped subset for the solve: diversity places, sharpness picks.

    Structural gates first (found, >= _MIN_CORNERS_FOR_CALIBRATION corners, not a
    near-collinear sliver — the solver's own hard floors). Then a two-pass
    selection (ADR-0038): farthest-point sampling over (tilt, image-x, image-y)
    picks ``cap`` diversity ANCHORS (where a keyframe is wanted); every candidate
    is assigned to its nearest anchor, and the SHARPEST candidate of each cell is
    kept. Diversity decides how many keyframes and where; sharpness decides which
    one to take at each spot. So a uniformly-blurry sweep still calibrates (no
    absolute blur gate), while coverage is never traded away for the crispest few
    (they cluster on the frontal/centre hold — anti-pattern ADR-0009).
    """
    width, height = image_size
    candidates = [
        d
        for d in detections
        if d.found
        and d.ids is not None
        and d.corners is not None
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

    # Pass 1 — diversity anchors: farthest-point sampling from the richest view,
    # each time adding the candidate farthest from the already-chosen anchors.
    start = int(max(range(len(candidates)), key=lambda i: candidates[i].count))
    anchors = [start]
    min_dist = np.linalg.norm(feats - feats[start], axis=1)
    while len(anchors) < cap:
        nxt = int(np.argmax(min_dist))
        anchors.append(nxt)
        min_dist = np.minimum(min_dist, np.linalg.norm(feats - feats[nxt], axis=1))

    # Pass 2 — sharpness within each anchor's cell: assign every candidate to its
    # nearest anchor, keep the sharpest per cell (an anchor is its own cell member,
    # so a lone cell degenerates to just keeping the anchor).
    anchor_feats = feats[anchors]
    best: dict[int, int] = {}  # anchor slot -> chosen candidate index
    for i in range(len(candidates)):
        slot = int(np.argmin(np.linalg.norm(anchor_feats - feats[i], axis=1)))
        current = best.get(slot)
        if current is None or candidates[i].sharpness > candidates[current].sharpness:
            best[slot] = i
    # In frame order, not in the order the sampling placed the anchors: that order
    # follows the tilt, hence the seed, and the solve must not depend on it.
    return [candidates[i] for i in sorted(best.values())]


def _cv_charuco_board(board: CalibrationBoard) -> cv2.aruco.CharucoBoard:
    return cv2.aruco.CharucoBoard(
        (board.columns, board.rows), 1.0, board.marker_ratio, resolve(board.dictionary)
    )


def calibrate_intrinsic(
    detections: list[BoardDetection],
    board: CalibrationBoard,
    image_size: tuple[int, int],
) -> IntrinsicResult:
    """Calibrate camera intrinsics from retained ChArUco detections.

    Raises ``ValueError`` for a non-ChArUco board, too few usable views, or an
    OpenCV solver failure on them.
    """
    if board.board_type is not BoardType.CHARUCO:
        raise ValueError("intrinsic calibration requires a ChArUco board")

    cv_board = _cv_charuco_board(board)
    object_points: list[NDArray[np.float32]] = []
    image_points: list[NDArray[np.float32]] = []
    used_sharpness: list[float] = []
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
        used_sharpness.append(det.sharpness)
        grid_count += int(obj.shape[0])

    if len(object_points) < _MIN_VIEWS:
        raise ValueError(f"need >= {_MIN_VIEWS} usable views, got {len(object_points)}")

    width, height = image_size
    # From Caliscope v0.11.5's seed (ADR-0053): it does not depend on the order of the
    # views, where OpenCV's own initialisation, from their homographies, does (1.40 to
    # 6.5 px on the same 50 views, by their order). That one only stands in when
    # the seeded solve raises or returns a non-finite cost. distCoeffs=None (and
    # cameraMatrix=None without the guess flag) is valid at runtime; the cv2 stub
    # types both as required, hence the ignore. A solve is an 8-tuple of mixed types.
    failures: list[str] = []
    solve: Any = None
    for seed, flags in ((guessed_camera_matrix(width, height), _CALIB_FLAGS), (None, 0)):
        try:
            solve = cv2.calibrateCameraExtended(  # type: ignore[call-overload]
                object_points, image_points, (width, height), seed, None, flags=flags
            )
        except cv2.error as exc:
            failures.append(str(exc).strip())
            solve = None
        else:
            if np.isfinite(solve[0]):
                break
            failures.append(f"non-finite cost {solve[0]}")
            solve = None
        source = "the seed" if seed is not None else "OpenCV's initialisation"
        logger.warning("intrinsic solve from %s failed: %s", source, failures[-1])
    if solve is None:
        # The solver's own assertions (degenerate views the gates above missed)
        # are a property of the sweep, not a service fault: report them like the
        # other unusable-input cases (ValueError -> 422) instead of a bare 500.
        raise ValueError(f"OpenCV calibration failed on these views: {'; then '.join(failures)}")
    rms, matrix, dist, rvecs, tvecs, _sdi, _sde, per_view = solve
    rvec_list = [np.asarray(r, np.float64) for r in rvecs]
    tvec_list = [np.asarray(t, np.float64) for t in tvecs]
    coverage = _coverage_map(image_points, (width, height))
    uncertainty = _uncertainty(
        object_points, image_points, matrix, dist, rvec_list, tvec_list, coverage, image_size
    )
    return IntrinsicResult(
        matrix=np.asarray(matrix, float).tolist(),
        distortions=np.asarray(dist, float).ravel().tolist(),
        error=float(rms),
        per_view_errors=np.asarray(per_view, float).ravel().tolist(),
        grid_count=grid_count,
        view_count=len(object_points),
        image_size=(width, height),
        coverage=coverage,
        image_coverage=_union_coverage(image_points, (width, height)),
        orientation_bins=_orientation_bins(rvec_list),
        board_quads=_board_quads(rvec_list, tvec_list, cv_board),
        sharpness_min=float(min(used_sharpness)),
        sharpness_median=float(np.median(used_sharpness)),
        **uncertainty,
    )


class _UncertaintyFields(TypedDict, total=False):
    uncertainty: tuple[tuple[float | None, ...], ...]
    uncertainty_covered_px: float | None
    uncertainty_uncovered_px: float | None
    uncertainty_unmodelled: float


def _uncertainty(
    object_points: list[NDArray[np.float32]],
    image_points: list[NDArray[np.float32]],
    matrix: NDArray[np.float64],
    dist: NDArray[np.float64],
    rvecs: list[NDArray[np.float64]],
    tvecs: list[NDArray[np.float64]],
    coverage: tuple[tuple[int, ...], ...],
    image_size: tuple[int, int],
) -> _UncertaintyFields:
    """The result's uncertainty fields (ADR-0055); empty when the covariance is singular.

    A diagnostic: a degenerate covariance must not cost the solve it describes. A nearly
    singular one is not caught: it reads as huge, finite figures.
    """
    distortions = np.asarray(dist, np.float64).ravel()
    try:
        covariances = intrinsic_covariances(
            object_points, image_points, matrix, distortions, rvecs, tvecs
        )
        grid = projection_uncertainty(
            covariances, matrix, distortions, np.asarray(coverage), image_size
        )
    except (np.linalg.LinAlgError, cv2.error) as exc:
        logger.warning("projection uncertainty skipped: %s", exc)
        return {}
    counts = np.asarray(coverage)
    modelled = np.isfinite(grid)

    def rms(mask: NDArray[np.bool_]) -> float | None:
        cells = mask & modelled
        return float(np.sqrt(np.mean(np.square(grid[cells])))) if cells.any() else None

    return {
        "uncertainty": tuple(
            tuple(round(float(v), 3) if np.isfinite(v) else None for v in row) for row in grid
        ),
        "uncertainty_covered_px": rms(counts >= ROBUST_COVERAGE),
        "uncertainty_uncovered_px": rms(counts == 0),
        "uncertainty_unmodelled": float(np.mean(~modelled)),
    }


def compute_intrinsic_from_video(
    video_path: Path,
    board: CalibrationBoard,
    *,
    cap: int,
    stride: int,
    frame_start: int = 0,
    frame_end: int | None = None,
) -> IntrinsicResult:
    """Recompute intrinsics from a recorded capture (ADR-0019/0022 record → prepare → compute).

    The operator bounds cost/quality in the *Prepare* step (ADR-0022): ``stride``
    ("detect 1 frame every N", the read decimation — detection, not the solve,
    dominates the cost), ``frame_start``/``frame_end`` trim the sweep (frame
    indices), and ``cap`` limits the kept keyframes. Defaults for omitted request
    fields resolve in the transport layer against TUNING (ADR-0036). Raises
    ``ValueError`` on an empty/unusable range (before the solver).
    """
    detector = BoardDetector(board)
    capture = cv2.VideoCapture(str(video_path))
    start = max(0, frame_start)
    # Without a trim, to the last decodable frame: the container's frame count is an
    # estimate, and on a variable-rate MKV it can announce half the frames (47 of 91).
    end = frame_end
    read_stride = max(1, stride)
    detections: list[BoardDetection] = []
    image_size: tuple[int, int] | None = None
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok or (end is not None and index >= end):
                break
            if index >= start and (index - start) % read_stride == 0:
                if image_size is None:
                    image_size = (frame.shape[1], frame.shape[0])
                detections.append(detector.detect(cast("NDArray[np.uint8]", frame)))
            index += 1
    finally:
        capture.release()

    if image_size is None:
        raise ValueError(f"no readable frames in {video_path}")
    keyframes = select_keyframes(detections, image_size, cap=cap)
    return calibrate_intrinsic(keyframes, board, image_size)
