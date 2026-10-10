"""Board motion between the captures of an extrinsic group (ADR-0056).

The cameras of a sweep do not expose at the same instant: a group pairs each member
with a frame within one sync window of its reference frame, and a board moving
meanwhile is seen at slightly different places by its members - an error the solve
cannot tell from geometry. Per member, the board's image speed times the member's
offset from the group instant is how many pixels it sits off that instant; a group
whose worst member is off by more than the gate is not used.

The speed comes from pyramidal Lucas-Kanade tracking of the detected corners into the
previous and next frames, which the compute's sequential decode walks anyway: ~2 ms
per member (two tracks and their grey conversions), 3-5 % of the walk. Differences
of detections gate as well, but cost two more detections per member.

Measured on Test, test and calib-07-13-2026 against held-out views (the pools and
their limits are in ADR-0056): the 0.5 px gate lowers the held-out reprojection
error by about 2 % on the first two and by 3 to 15 % on the third, whose held-out
rigidity degrades on 3 pools of 4.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

# Neighbours further apart than this do not measure an instantaneous velocity (the
# board can turn around within a quarter second of a hand-held sweep).
_MAX_VELOCITY_BASELINE_S = 0.25
# Pyramidal LK on a crop around the corners: 21 px window, 4 levels (follows a large
# marker's corners up to ~150 px between frames), and a crop margin for that motion.
# A small marker moved that far can be lost under a valid status: its speed then
# reads high and the group is dropped, the safe side.
_LK_WINDOW = (21, 21)
_LK_LEVELS = 4
_LK_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 30, 0.01)
_LK_MARGIN_PX = 160


@dataclass(frozen=True)
class MemberMotion:
    """One group member's capture time and board image speed."""

    timestamp: float  # capture stamp of the member's frame (s)
    speed_px_s: float | None  # fastest corner's image speed; None = unknown


def track_corners_lk(
    source: NDArray[np.uint8], target: NDArray[np.uint8], points: NDArray[np.float64]
) -> NDArray[np.float64]:
    """``points`` of the grayscale ``source`` frame found in ``target`` (NaN = lost).

    Runs on the crop around the points only, so the pyramid costs under 1 ms at 1080p
    whatever the frame size.
    """
    height, width = source.shape[:2]
    x0 = max(0, int(np.floor(points[:, 0].min() - _LK_MARGIN_PX)))
    y0 = max(0, int(np.floor(points[:, 1].min() - _LK_MARGIN_PX)))
    x1 = min(width, int(np.ceil(points[:, 0].max() + _LK_MARGIN_PX)))
    y1 = min(height, int(np.ceil(points[:, 1].max() + _LK_MARGIN_PX)))
    offset = np.array([x0, y0], np.float64)
    local = (points - offset).reshape(-1, 1, 2).astype(np.float32)
    # nextPts is only an initial guess under OPTFLOW_USE_INITIAL_FLOW (not set); a copy
    # keeps the stub's required argument honest.
    moved, status, _ = cv2.calcOpticalFlowPyrLK(
        np.ascontiguousarray(source[y0:y1, x0:x1]),
        np.ascontiguousarray(target[y0:y1, x0:x1]),
        local,
        local.copy(),
        winSize=_LK_WINDOW,
        maxLevel=_LK_LEVELS,
        criteria=_LK_CRITERIA,
    )
    out = np.asarray(moved, np.float64).reshape(-1, 2) + offset
    out[np.asarray(status).reshape(-1) == 0] = np.nan
    return out


def lk_speed_px_s(
    corners: NDArray[np.float64],
    in_previous: NDArray[np.float64] | None,
    in_next: NDArray[np.float64] | None,
    dt_previous: float | None,
    dt_next: float | None,
) -> float | None:
    """Board image speed (px/s, fastest corner) from the corners' tracks.

    ``in_previous`` / ``in_next`` are the frame's corners as found in frames i - 1 /
    i + 1, ``dt_*`` the stamp gaps. Central difference when both neighbours tracked,
    else one-sided; a gap that is not positive is no baseline. None when nothing
    tracked or the gap is too long to measure an instantaneous velocity.
    """
    before = dt_previous if dt_previous is not None and dt_previous > 0.0 else None
    after = dt_next if dt_next is not None and dt_next > 0.0 else None
    options: list[tuple[NDArray[np.float64], NDArray[np.float64], float]] = []
    if in_previous is not None and in_next is not None and before and after:
        options.append((in_previous, in_next, before + after))
    if in_next is not None and after:
        options.append((corners, in_next, after))
    if in_previous is not None and before:
        options.append((in_previous, corners, before))
    for start, end, baseline in options:
        if not 0.0 < baseline <= _MAX_VELOCITY_BASELINE_S:
            continue
        valid = np.isfinite(start).all(axis=1) & np.isfinite(end).all(axis=1)
        if bool(valid.any()):
            return float(np.linalg.norm(end[valid] - start[valid], axis=1).max() / baseline)
    return None


def group_motion_px(members: Mapping[str, MemberMotion]) -> float | None:
    """Worst member's displacement (px) between its capture and the group instant.

    ``speed x |t_member - t_group|``, ``t_group`` the detected members' mean stamp: to first
    order, how far each member's corners are from where the board was at the instant
    the group stands for. None when a member's speed is unknown.
    """
    if not members:
        return None
    instant = sum(member.timestamp for member in members.values()) / len(members)
    worst = 0.0
    for member in members.values():
        if member.speed_px_s is None:
            return None
        worst = max(worst, member.speed_px_s * abs(member.timestamp - instant))
    return worst


def still_groups(
    motion_px: Sequence[float | None], *, max_motion_px: float, min_groups: int
) -> list[bool]:
    """Which groups the motion gate keeps.

    A group whose worst member moved more than ``max_motion_px`` is dropped; unknown
    motion (no usable neighbour) is kept. An import whose members share one stamp has
    zero motion, known.
    When fewer than ``min_groups`` survive, the stillest dropped groups top the pool
    up, so a hurried sweep degrades to "the stillest groups available" instead of
    starving the pairwise initialisation.
    """
    keep = [motion is None or motion <= max_motion_px for motion in motion_px]
    shortfall = min(min_groups, len(keep)) - sum(keep)
    if shortfall > 0:
        dropped = sorted(
            (i for i, kept in enumerate(keep) if not kept), key=lambda i: motion_px[i] or 0.0
        )
        for i in dropped[:shortfall]:
            keep[i] = True
    return keep
