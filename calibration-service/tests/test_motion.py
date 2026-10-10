"""Board motion between the captures of an extrinsic group (ADR-0056)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.calibration.motion import (
    MemberMotion,
    group_motion_px,
    lk_speed_px_s,
    still_groups,
    track_corners_lk,
)
from calibration_service.detection import BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard

MARKER = CalibrationBoard(
    board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=1, rows=1, marker_id=8
)


def test_lk_speed_is_a_central_difference_of_the_fastest_corner() -> None:
    # Asymmetric steps tell the central difference (75 px/s) from either one-sided
    # one (50 or 100), and a turning board's faster corner from the mean of the two.
    corners = np.array([[50.0, 50.0], [60.0, 50.0]])
    before = corners - np.array([[2.0, 0.0], [1.0, 0.0]])
    after = corners + np.array([[4.0, 0.0], [2.0, 0.0]])
    assert lk_speed_px_s(corners, before, after, 0.04, 0.04) == pytest.approx(75.0)
    assert lk_speed_px_s(corners, None, after, None, 0.04) == pytest.approx(100.0)
    assert lk_speed_px_s(corners, before, None, 0.04, None) == pytest.approx(50.0)
    lost = after.copy()
    lost[0] = np.nan  # the fast corner lost by LK: the other still measures
    assert lk_speed_px_s(corners, None, lost, None, 0.04) == pytest.approx(50.0)
    # A gap that is not positive is no baseline, even when the sum would be.
    assert lk_speed_px_s(corners, before, after, -0.01, 0.04) == pytest.approx(100.0)
    assert lk_speed_px_s(corners, None, None, None, None) is None


def test_neighbours_too_far_apart_measure_no_speed() -> None:
    corners = np.array([[50.0, 50.0]])
    assert lk_speed_px_s(corners, None, corners + 1.0, None, 0.3) is None


def test_group_motion_is_speed_times_offset_from_the_group_instant() -> None:
    members = {
        "cam_0": MemberMotion(timestamp=0.000, speed_px_s=100.0),
        "cam_1": MemberMotion(timestamp=0.010, speed_px_s=100.0),
        "cam_2": MemberMotion(timestamp=0.030, speed_px_s=100.0),
    }
    # instant = 13.33 ms; worst member cam_2: 100 px/s x 16.67 ms = 1.667 px
    assert group_motion_px(members) == pytest.approx(1.6667, abs=1e-3)
    held = {n: MemberMotion(timestamp=0.5, speed_px_s=900.0) for n in ("a", "b")}
    assert group_motion_px(held) == 0.0  # a parity import: one stamp per slot
    unknown = {"a": MemberMotion(0.0, None), "b": MemberMotion(0.01, 5.0)}
    assert group_motion_px(unknown) is None


def test_the_gate_drops_moving_groups_and_keeps_unknown_motion() -> None:
    assert still_groups([0.1, 3.0, 0.4, None, 0.6], max_motion_px=0.5, min_groups=0) == [
        True,
        False,
        True,
        True,
        False,
    ]


def test_a_starved_gate_tops_up_with_the_stillest_dropped_groups() -> None:
    motion = [None, 5.0, 2.0, 9.0, 0.3, 1.0]
    keep = still_groups(motion, max_motion_px=0.5, min_groups=4)
    assert keep == [True, False, True, False, True, True]  # + 1.0 and 2.0 px, the stillest


def test_the_gate_keeps_everything_when_every_group_holds_still() -> None:
    assert all(still_groups([0.0] * 20, max_motion_px=0.5, min_groups=5))


def _marker_frame(shift: tuple[float, float]) -> NDArray[np.uint8]:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)
    page = np.full((720, 960), 255, np.uint8)
    page[200:500, 300:600] = cv2.aruco.generateImageMarker(dictionary, 8, 300)
    matrix = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]])
    moved = cv2.warpAffine(page, matrix, (960, 720), flags=cv2.INTER_LINEAR, borderValue=255)
    return np.asarray(moved, np.uint8)


@pytest.mark.parametrize("shift", [(7.3, -4.6), (47.0, -31.0)])
def test_lk_finds_the_detected_corners_after_a_known_shift(shift: tuple[float, float]) -> None:
    # 47 px is beyond the 21 px window: only the pyramid levels follow it.
    first, second = _marker_frame((0.0, 0.0)), _marker_frame(shift)
    detection = BoardDetector(MARKER).detect(first)
    assert detection.corners is not None
    tracked = track_corners_lk(first, second, detection.corners.astype(np.float64))
    assert np.allclose(tracked - detection.corners, shift, atol=0.1)
