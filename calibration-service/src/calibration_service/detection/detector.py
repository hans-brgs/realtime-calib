"""Board detection on a captured frame ([[board-observation]], [[coverage-metrics]]).

Detects the ChArUco corners (or the single ArUco marker) and derives the two live
metrics that guide the operator during capture: ``fill_fraction`` (how much of the
frame the board covers — a distance proxy) and ``sharpness`` (Laplacian variance on
the board ROI). Runs at the resolution it is handed: the live loops detect on the
downscaled preview frame (ADR-0038), the computes on the native recording.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.board.dictionaries import resolve
from calibration_service.detection.border_refine import (
    BorderRefusal,
    marker_bits,
    refine_marker_corners,
)
from calibration_service.models.board import BoardType, CalibrationBoard

if TYPE_CHECKING:
    from cv2.typing import MatLike

logger = logging.getLogger(__name__)

_MIN_CORNERS = 4  # below this a frame is not useful for calibration

# Sub-pixel refinement of the *chessboard* corners (as Caliscope does) — NOT ArUco
# corner refinement, which OpenCV warns degrades ChArUco interpolation. It is also
# load-bearing on OpenCV 4.13.0: CharucoDetector.detectBoard returns the corners
# offset by ~+0.5 px (opencv#25539, fixed on 4.x after that tag) — measured +0.48 px
# on a rendered board, -0.01 px after this pass. Dropping it would bias cx/cy.
_SUBPIX_WIN = (11, 11)
_SUBPIX_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.0001)

# Crop around the target marker for the CONTOUR-refinement fallback (see
# BoardDetector._refine_on_crop). A tight crop changes how detectMarkers filters
# candidates near the crop edge — on a missed frame, minDistanceToBorder=0 or
# minMarkerDistanceRate=0.01 brings the target back — so the margin must keep
# the target well inside. Measured on every frame of session calib-07-13-2026
# (n=2782): 7.4 % missed at 0.3x the marker side, 0.1 % at 0.5x, none at 0.75x.
# At 0.75x the crop refinement matches the full-frame one: median 0.006 px,
# p99 0.021 px, max 0.049 px.
_CROP_MARGIN_RATIO = 0.75  # of the marker's longest side
_CROP_MARGIN_MIN_PX = 32


def _detector_params(
    *, single_marker: bool = False, refine: bool = True
) -> cv2.aruco.DetectorParameters:
    """ArUco detection tuned for small/peripheral markers on a wide-angle lens.

    Grounded in Caliscope + OpenCV docs: lower ``minMarkerPerimeterRate`` recovers
    small markers, higher ``polygonalApproxAccuracyRate`` tolerates barrel
    distortion.

    Corner refinement splits by use (ADR-0043): OFF on the ChArUco path (the
    chessboard interpolation + ``cornerSubPix`` do the precision work, and OpenCV
    warns ArUco refinement degrades it), CONTOUR on the single-marker path, where it
    line-fits the quad edges on the contour pixels and intersects them: less corner
    scatter than NONE, but every corner 1-2 px inward. That is final for the live
    overlay; the computes only take it as the seed of the border refinement
    (ADR-0052). SUBPIX had no effect (its saddle-point model fits chessboard
    X-corners, not marker L-corners). ``refine=False`` builds the unrefined
    single-marker detector the CONTOUR fallback locates the target with.
    """
    params = cv2.aruco.DetectorParameters()
    params.minMarkerPerimeterRate = 0.01  # default 0.03 — small / far markers
    params.polygonalApproxAccuracyRate = 0.05  # default 0.03 — distorted images
    if single_marker and refine:
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_CONTOUR
    return params


# eq=False: the generated __eq__ would compare ndarrays element-wise and raise
# ("truth value of an array is ambiguous") on ==, `in` or list.index.
@dataclass(frozen=True, eq=False)
class BoardDetection:
    """One board detection in a frame (corners/outline in the pixels of the frame it ran on)."""

    found: bool
    corners: NDArray[np.float32] | None  # (N, 2) sub-pixel corner positions
    ids: NDArray[np.int32] | None  # (N,) corner / marker ids
    outline: NDArray[np.float32] | None  # (4, 2) physical board contour (extrapolated)
    board_coverage: float  # outline area clipped to frame / frame area (calib.io >= 0.5)
    sharpness: float  # variance of the Laplacian over the board ROI
    tilt_deg: float | None  # board tilt vs the frontal plane (PnP, guessed K); 0 = frontal

    @property
    def count(self) -> int:
        return 0 if self.corners is None else int(self.corners.shape[0])

    @staticmethod
    def empty() -> BoardDetection:
        return BoardDetection(
            found=False,
            corners=None,
            ids=None,
            outline=None,
            board_coverage=0.0,
            sharpness=0.0,
            tilt_deg=None,
        )


def _to_gray(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    if image.ndim == 2:
        return image
    return cast("NDArray[np.uint8]", cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))


def _charuco_outline(
    corners: NDArray[np.float32], ids: NDArray[np.int32], columns: int, rows: int
) -> NDArray[np.float32] | None:
    """Extrapolate the physical board contour from the detected interior corners.

    ChArUco only detects the interior chessboard corners — a rectangle inset by one
    square from the physical edge. We fit the plane→image homography from the known
    grid indices (from corner ids) and project the 4 board-edge corners (one grid
    step beyond the outermost interior corners). Works under perspective/tilt.
    """
    nx, ny = columns - 1, rows - 1  # interior-corner grid dimensions
    grid = np.column_stack([ids % nx, ids // nx]).astype(np.float32)
    homography, _ = cv2.findHomography(grid, corners)
    if homography is None:
        return None
    outline_grid = np.array([[[-1, -1], [nx, -1], [nx, ny], [-1, ny]]], np.float32)
    return cast("NDArray[np.float32]", cv2.perspectiveTransform(outline_grid, homography)[0])


def guessed_camera_matrix(width: int, height: int) -> NDArray[np.float64]:
    """A rough pinhole K when none is known yet: Caliscope v0.11.5's seed (ADR-0053).

    Focal = the longer image side (~53° HFOV on a landscape frame), principal point
    at the image centre in pixel-centre coordinates. ONE definition for both users
    (ADR-0036 — it used to exist twice): the live tilt metric's pre-calibration PnP
    (approximate but monotonic, enough to guide the operator) and the intrinsic
    solve's CALIB_USE_INTRINSIC_GUESS seed. The tilt is recomputed exactly at
    compute time with the real intrinsics.
    """
    f = float(max(width, height))
    return np.array(
        [[f, 0.0, (width - 1) / 2], [0.0, f, (height - 1) / 2], [0.0, 0.0, 1.0]], np.float64
    )


def _tilt_deg(
    object_points: NDArray[np.float32],
    image_points: NDArray[np.float32],
    width: int,
    height: int,
    *,
    square: bool = False,
) -> float | None:
    """Board tilt vs the frontal plane (degrees) from a planar PnP pose (guessed K).

    ``square`` (single ArUco marker, exactly 4 corners) uses IPPE_SQUARE, the
    square-specific planar solver; otherwise IPPE (planar, >= 4 coplanar points).
    Returns None on degenerate input (too few / collinear) so it never crashes capture.
    """
    n = object_points.shape[0]
    if square:
        if n != 4:
            return None
        flag = cv2.SOLVEPNP_IPPE_SQUARE
    else:
        if n < 4:
            return None
        if len(np.unique(object_points[:, 0])) < 2 or len(np.unique(object_points[:, 1])) < 2:
            return None  # collinear → pose ill-defined
        flag = cv2.SOLVEPNP_IPPE
    camera_matrix = guessed_camera_matrix(width, height)
    try:
        ok, rvec, _ = cv2.solvePnP(object_points, image_points, camera_matrix, None, flags=flag)
    except cv2.error:
        return None
    if not ok:
        return None
    rotation, _ = cv2.Rodrigues(rvec)
    # Board normal's z-component in camera frame; |z|=1 when frontal.
    return float(np.degrees(np.arccos(min(1.0, abs(float(rotation[2, 2]))))))


def _coverage(outline: NDArray[np.float32], width: int, height: int) -> float:
    """Area of the board outline clipped to the frame, as a fraction of the frame."""
    if not width or not height:
        return 0.0
    rect = np.array([[0, 0], [width, 0], [width, height], [0, height]], np.float32)
    try:
        area, _ = cv2.intersectConvexConvex(outline.astype(np.float32), rect)
    except cv2.error:
        return 0.0
    return float(area) / float(width * height)


def _sharpness(gray: NDArray[np.uint8], corners: NDArray[np.float32]) -> float:
    x, y, w, h = cv2.boundingRect(corners.astype(np.int32))
    if w < 3 or h < 3:
        return 0.0
    roi = gray[y : y + h, x : x + w]
    return float(cv2.Laplacian(roi, cv2.CV_64F).var())


class BoardDetector:
    """Reusable detector for a fixed board (build the OpenCV objects once).

    ``border_refine`` (single ArUco marker only, ADR-0052): replace CONTOUR's corners,
    1-2 px too far inward on real footage (0.96 to 1.85 px against ChArUco ground
    truth), by the self-calibrated border refinement.
    For the computes: the live overlay keeps CONTOUR, cheaper, whose bias a preview
    never shows. A view the refinement refuses is dropped, never kept with CONTOUR's
    biased corners; ``border_refusals`` counts them by reason.
    """

    def __init__(self, board: CalibrationBoard, *, border_refine: bool = False) -> None:
        self._board = board
        self._border_bits: NDArray[np.bool_] | None = None
        self.border_refusals: Counter[str] = Counter()
        self.border_attempts = 0  # single-marker views the border refinement ran on
        dictionary = resolve(board.dictionary)
        if board.board_type is BoardType.CHARUCO:
            cv_board = cv2.aruco.CharucoBoard(
                (board.columns, board.rows), 1.0, board.marker_ratio, dictionary
            )
            charuco_params = cv2.aruco.CharucoParameters()
            charuco_params.tryRefineMarkers = True  # recover markers from interpolation
            self._charuco: cv2.aruco.CharucoDetector | None = cv2.aruco.CharucoDetector(
                cv_board, charucoParams=charuco_params, detectorParams=_detector_params()
            )
            self._aruco: cv2.aruco.ArucoDetector | None = None
            self._aruco_unrefined: cv2.aruco.ArucoDetector | None = None
            # 3D corner coords (board units), indexed by ChArUco corner id — for PnP.
            self._object_points = np.asarray(cv_board.getChessboardCorners(), np.float32)
        else:
            self._charuco = None
            self._aruco = cv2.aruco.ArucoDetector(
                dictionary, _detector_params(single_marker=True)
            )
            self._aruco_unrefined = cv2.aruco.ArucoDetector(
                dictionary, _detector_params(single_marker=True, refine=False)
            )
            if border_refine:
                self._border_bits = marker_bits(dictionary, board.marker_id)
            # Single marker: canonical centered square (TL, TR, BR, BL) for IPPE_SQUARE,
            # matching the corner order cv2.aruco returns.
            self._object_points = np.array(
                [[-0.5, 0.5, 0], [0.5, 0.5, 0], [0.5, -0.5, 0], [-0.5, -0.5, 0]], np.float32
            )
        # Recovery paths taken (each logged loud once, then quiet).
        self._refine_failures = 0  # CONTOUR fallbacks
        self._subpix_failures = 0  # ChArUco views dropped

    def detect(self, image: NDArray[np.uint8]) -> BoardDetection:
        gray = _to_gray(image)
        if self._board.inverted:
            # An inverted target is printed white-on-black (render_board_png): give
            # OpenCV the black-on-white pattern it detects (spec calibration-board;
            # Caliscope does the same: v0.5.4 ``gray = ~gray``, v0.11.5
            # ``cv2.bitwise_not``). Laplacian variance is unchanged.
            gray = cast("NDArray[np.uint8]", cv2.bitwise_not(gray))
        height, width = gray.shape[:2]

        corners: NDArray[np.float32] | None = None
        ids: NDArray[np.int32] | None = None
        if self._charuco is not None:
            corners_raw, ids_raw, _, _ = self._charuco.detectBoard(gray)
            if corners_raw is not None and corners_raw.shape[0] >= 1 and ids_raw is not None:
                # Sub-pixel refine the interpolated chessboard corners (calibration-grade).
                refined = np.ascontiguousarray(corners_raw, dtype=np.float32)
                try:
                    cv2.cornerSubPix(gray, refined, _SUBPIX_WIN, (-1, -1), _SUBPIX_CRITERIA)
                except cv2.error:
                    # Unrefined corners carry the detectBoard offset (see
                    # _SUBPIX_WIN): no observation beats a biased one — but a
                    # dropped observation is said out loud (once, then quietly).
                    self._subpix_failures += 1
                    log = logger.warning if self._subpix_failures == 1 else logger.debug
                    log(
                        "cornerSubPix raised; ChArUco view dropped (%d so far)",
                        self._subpix_failures,
                        exc_info=True,
                    )
                else:
                    corners = refined.reshape(-1, 2).astype(np.float32)
                    ids = ids_raw.reshape(-1).astype(np.int32)
        else:
            corners, ids = self._detect_single_marker(gray)

        if corners is None or corners.shape[0] < _MIN_CORNERS:
            return BoardDetection.empty()

        if self._charuco is not None and ids is not None:
            outline = _charuco_outline(corners, ids, self._board.columns, self._board.rows)
        else:
            # Single ArUco marker: the 4 detected corners already are the board contour.
            outline = corners if corners.shape[0] == 4 else None

        coverage = _coverage(outline, width, height) if outline is not None else 0.0

        is_square = self._charuco is None  # single ArUco marker
        if not is_square and ids is not None:
            object_points = self._object_points[ids]
        else:
            object_points = self._object_points
        tilt = (
            _tilt_deg(object_points, corners, width, height, square=is_square)
            if object_points.shape[0] == corners.shape[0]
            else None
        )

        return BoardDetection(
            found=True,
            corners=corners,
            ids=ids,
            outline=outline,
            board_coverage=coverage,
            sharpness=_sharpness(gray, corners),
            tilt_deg=tilt,
        )

    def _detect_single_marker(
        self, gray: NDArray[np.uint8]
    ) -> tuple[NDArray[np.float32] | None, NDArray[np.int32] | None]:
        assert self._aruco is not None
        try:
            marker_corners, marker_ids, _ = self._aruco.detectMarkers(gray)
        except cv2.error:
            # OpenCV's CONTOUR refinement asserts on some degenerate candidate
            # contours (4.13: "nContours.size() >= 2 in _interpolate2Dline"), seen
            # on real sweeps for a ~7 px false-positive marker elsewhere in the
            # frame. One bad candidate must not cost the frame — nor, unhandled,
            # the whole compute (it surfaced as an HTTP 500).
            corners = self._refine_on_crop(gray)
        else:
            corners = self._target_corners(marker_corners, marker_ids)
        if corners is not None and self._border_bits is not None:
            corners = self._border_corners(gray, corners, self._border_bits)
        if corners is None:
            return None, None
        ids = np.full(corners.shape[0], self._board.marker_id, dtype=np.int32)
        return corners, ids

    def _border_corners(
        self, gray: NDArray[np.uint8], seed: NDArray[np.float32], bits: NDArray[np.bool_]
    ) -> NDArray[np.float32] | None:
        """Border-refined corners (ADR-0052), or None when the view is unreliable.

        ``gray`` is the image the marker was detected in, already inverted for an
        inverted target. An OpenCV or numpy error counts as a refusal of its own: one
        view must not cost the compute. A few refused views are normal (0.6 to 5.4 % of a
        hand-held sweep), so each is only a DEBUG line; the compute logs the summary.
        """
        self.border_attempts += 1
        try:
            outcome = refine_marker_corners(gray, seed, bits)
        except (cv2.error, np.linalg.LinAlgError):
            logger.debug("border refinement raised", exc_info=True)
            outcome = BorderRefusal.NUMERICAL_FAILURE
        if isinstance(outcome, BorderRefusal):
            self.border_refusals[outcome.value] += 1
            logger.debug("single-marker view dropped (%s)", outcome.value)
            return None
        return outcome.corners

    def _target_corners(
        self, marker_corners: Sequence[MatLike], marker_ids: MatLike | None
    ) -> NDArray[np.float32] | None:
        """The board's marker among a ``detectMarkers`` output, as (4, 2) corners.

        ``marker_ids`` is None when nothing was detected (the cv2 stubs omit it).
        """
        if marker_ids is None:
            return None
        matches = np.where(np.asarray(marker_ids).reshape(-1) == self._board.marker_id)[0]
        if matches.size == 0:
            return None
        return np.asarray(marker_corners[int(matches[0])], np.float32).reshape(-1, 2)

    def _refine_on_crop(self, gray: NDArray[np.uint8]) -> NDArray[np.float32] | None:
        """CONTOUR-refined target corners when the full-frame refinement raised.

        Locates the target without refinement, then re-runs the refining detector
        on a crop around it, so a candidate that tripped OpenCV elsewhere in the
        frame is out of the picture. With the margin above, the crop refinement
        is equivalent to the full-frame one (see _CROP_MARGIN_RATIO), so these
        corners are calibration observations like any other. The frame is
        dropped when the crop still trips the refinement (the culprit sits next
        to the target, or is the target) — not a view to calibrate on.
        """
        assert self._aruco is not None and self._aruco_unrefined is not None
        self._refine_failures += 1
        log = logger.warning if self._refine_failures == 1 else logger.debug
        log(
            "ArUco CONTOUR refinement raised on a frame (%d so far); "
            "refining the target marker alone",
            self._refine_failures,
        )
        raw_corners, raw_ids, _ = self._aruco_unrefined.detectMarkers(gray)
        raw = self._target_corners(raw_corners, raw_ids)
        if raw is None:
            return None
        side = max(float(np.linalg.norm(raw[i] - raw[(i + 1) % 4])) for i in range(4))
        margin = max(_CROP_MARGIN_RATIO * side, _CROP_MARGIN_MIN_PX)
        height, width = gray.shape[:2]
        x0 = max(0, int(np.floor(raw[:, 0].min() - margin)))
        y0 = max(0, int(np.floor(raw[:, 1].min() - margin)))
        x1 = min(width, int(np.ceil(raw[:, 0].max() + margin)))
        y1 = min(height, int(np.ceil(raw[:, 1].max() + margin)))
        try:
            crop_corners, crop_ids, _ = self._aruco.detectMarkers(
                np.ascontiguousarray(gray[y0:y1, x0:x1])
            )
        except cv2.error:
            logger.debug("CONTOUR refinement raised on the crop around the target; frame dropped")
            return None
        corners = self._target_corners(crop_corners, crop_ids)
        if corners is None:
            return None
        return (corners + np.array([x0, y0], np.float32)).astype(np.float32)
