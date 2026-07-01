"""Board detection on a captured frame ([[board-observation]], [[coverage-metrics]]).

Detects the ChArUco corners (or the single ArUco marker) and derives the two live
metrics that guide the operator during capture: ``fill_fraction`` (how much of the
frame the board covers — a distance proxy) and ``sharpness`` (Laplacian variance on
the board ROI — the blur gate). Runs at the native capture resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.board.dictionaries import resolve
from calibration_service.models.board import BoardType, CalibrationBoard

_MIN_CORNERS = 4  # below this a frame is not useful for calibration


@dataclass(frozen=True)
class BoardDetection:
    """One board detection in a frame (corners at native resolution)."""

    found: bool
    corners: NDArray[np.float32] | None  # (N, 2) sub-pixel corner positions
    ids: NDArray[np.int32] | None  # (N,) corner / marker ids
    fill_fraction: float  # convex-hull area of corners / frame area
    sharpness: float  # variance of the Laplacian over the board ROI

    @property
    def count(self) -> int:
        return 0 if self.corners is None else int(self.corners.shape[0])

    @staticmethod
    def empty() -> BoardDetection:
        return BoardDetection(found=False, corners=None, ids=None, fill_fraction=0.0, sharpness=0.0)


def _to_gray(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    if image.ndim == 2:
        return image
    return cast("NDArray[np.uint8]", cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))


def _fill_fraction(corners: NDArray[np.float32], width: int, height: int) -> float:
    area = float(cv2.contourArea(cv2.convexHull(corners)))
    return area / float(width * height) if width and height else 0.0


def _sharpness(gray: NDArray[np.uint8], corners: NDArray[np.float32]) -> float:
    x, y, w, h = cv2.boundingRect(corners.astype(np.int32))
    if w < 3 or h < 3:
        return 0.0
    roi = gray[y : y + h, x : x + w]
    return float(cv2.Laplacian(roi, cv2.CV_64F).var())


class BoardDetector:
    """Reusable detector for a fixed board (build the OpenCV objects once)."""

    def __init__(self, board: CalibrationBoard) -> None:
        self._board = board
        dictionary = resolve(board.dictionary)
        if board.board_type is BoardType.CHARUCO:
            cv_board = cv2.aruco.CharucoBoard(
                (board.columns, board.rows), 1.0, board.marker_ratio, dictionary
            )
            self._charuco: cv2.aruco.CharucoDetector | None = cv2.aruco.CharucoDetector(cv_board)
            self._aruco: cv2.aruco.ArucoDetector | None = None
        else:
            self._charuco = None
            self._aruco = cv2.aruco.ArucoDetector(dictionary)

    def detect(self, image: NDArray[np.uint8]) -> BoardDetection:
        gray = _to_gray(image)
        height, width = gray.shape[:2]

        corners: NDArray[np.float32] | None = None
        ids: NDArray[np.int32] | None = None
        if self._charuco is not None:
            corners_raw, ids_raw, _, _ = self._charuco.detectBoard(gray)
            if corners_raw is not None:
                corners = corners_raw.reshape(-1, 2).astype(np.float32)
            if ids_raw is not None:
                ids = ids_raw.reshape(-1).astype(np.int32)
        else:
            corners, ids = self._detect_single_marker(gray)

        if corners is None or corners.shape[0] < _MIN_CORNERS:
            return BoardDetection.empty()

        return BoardDetection(
            found=True,
            corners=corners,
            ids=ids,
            fill_fraction=_fill_fraction(corners, width, height),
            sharpness=_sharpness(gray, corners),
        )

    def _detect_single_marker(
        self, gray: NDArray[np.uint8]
    ) -> tuple[NDArray[np.float32] | None, NDArray[np.int32] | None]:
        assert self._aruco is not None
        marker_corners, marker_ids, _ = self._aruco.detectMarkers(gray)
        if marker_ids is None:
            return None, None
        flat = marker_ids.reshape(-1)
        matches = np.where(flat == self._board.marker_id)[0]
        if matches.size == 0:
            return None, None
        corners = marker_corners[int(matches[0])].reshape(-1, 2).astype(np.float32)
        ids = np.full(corners.shape[0], self._board.marker_id, dtype=np.int32)
        return corners, ids
