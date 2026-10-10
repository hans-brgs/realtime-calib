"""Pre-export checks (ADR-0057): what each one proves, and what it does not.

Pure functions over the session, its solved result and the persisted bundle-adjustment
observations. Each check reports a status (``ok``, ``warn``, ``fail``, or
``unavailable`` when its inputs are missing), its value, the thresholds it was judged
against, its scope (``internal``: computed from the solve itself, so it cannot see a
bias the solve shares; ``external``: against something measured apart) and a detail
saying what it proves. None blocks an export.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic import BAInputs, ExtrinsicResult
from calibration_service.export.opencv import WorldFrame
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession

logger = logging.getLogger(__name__)

# Per-camera extrinsic error at the output resolution: the bands of the 3D review
# (spec 3d-extrinsic-review), green up to the first, amber up to the second.
CAMERA_ERROR_PX = (0.6, 1.2)
# Median symmetric epipolar distance of a pair, output px; pairs with fewer common
# observations are not judged.
EPIPOLAR_MEDIAN_PX = (0.5, 1.0)
EPIPOLAR_MIN_SHARED = 20
# RMS deviation of the triangulated target from its rigid shape, as a share of its
# longest side: the lot 4 solves sit at 0.13-0.15 %, the CONTOUR ones at 0.3-0.4 %.
RIGIDITY_SHARE = (0.0025, 0.01)


@dataclass(frozen=True)
class Check:
    id: str
    status: str  # "ok" | "warn" | "fail" | "unavailable"
    value: float | None
    thresholds: list[float]  # [green up to, amber up to]; empty when not banded
    scope: str  # "internal" | "external"
    detail: str
    items: dict[str, float] = field(default_factory=dict)  # per camera / per pair


def _band(value: float, bands: tuple[float, float]) -> str:
    return "ok" if value <= bands[0] else "warn" if value <= bands[1] else "fail"


def camera_error_check(session: CalibrationSession) -> Check:
    """The worst camera's extrinsic error, output px (the review's bands)."""
    errors = {
        c.name: float(c.extrinsic_error) for c in session.cameras if c.extrinsic_error is not None
    }
    if not errors:
        return Check(
            "camera_error",
            "unavailable",
            None,
            list(CAMERA_ERROR_PX),
            "internal",
            "no extrinsic error recorded",
        )
    worst = max(errors, key=lambda n: errors[n])
    return Check(
        "camera_error",
        _band(errors[worst], CAMERA_ERROR_PX),
        errors[worst],
        list(CAMERA_ERROR_PX),
        "internal",
        f"worst camera {worst}: the reprojection error of the solve's own observations",
        errors,
    )


def _relative_essential(result: ExtrinsicResult, a: str, b: str) -> NDArray[np.float64]:
    """E with n_b^T E n_a = 0 for normalised points of cameras a and b."""
    ra = np.asarray(cv2.Rodrigues(np.asarray(result.rotations[a]))[0], np.float64)
    rb = np.asarray(cv2.Rodrigues(np.asarray(result.rotations[b]))[0], np.float64)
    ta = np.asarray(result.translations[a], np.float64)
    tb = np.asarray(result.translations[b], np.float64)
    rotation = rb @ ra.T
    t = tb - rotation @ ta
    cross = np.array([[0.0, -t[2], t[1]], [t[2], 0.0, -t[0]], [-t[1], t[0], 0.0]])
    essential: NDArray[np.float64] = cross @ rotation
    return essential


def _line_distance(points: NDArray[np.float64], lines: NDArray[np.float64]) -> NDArray[np.float64]:
    numerator = np.abs(np.sum(points * lines, axis=1))
    return np.asarray(numerator / np.hypot(lines[:, 0], lines[:, 1]), np.float64)


def epipolar_check(
    session: CalibrationSession, result: ExtrinsicResult | None, ba: BAInputs | None
) -> Check:
    """Per pair, the median symmetric epipolar distance of the points both saw."""
    if result is None:
        return Check(
            "epipolar",
            "unavailable",
            None,
            list(EPIPOLAR_MEDIAN_PX),
            "internal",
            "no extrinsic solve",
        )
    if ba is None or not ba.obs_camera:
        return Check(
            "epipolar",
            "unavailable",
            None,
            list(EPIPOLAR_MEDIAN_PX),
            "internal",
            "no persisted observations: recompute the extrinsics",
        )
    focal = {c.name: 0.5 * (c.matrix[0][0] + c.matrix[1][1]) for c in session.cameras if c.matrix}
    seen: dict[int, dict[str, NDArray[np.float64]]] = defaultdict(dict)
    for camera, point, norm in zip(ba.obs_camera, ba.obs_point, ba.obs_norm, strict=True):
        seen[point][result.cameras[camera]] = np.array([norm[0], norm[1], 1.0])
    medians: dict[str, float] = {}
    tails: list[str] = []
    for a, b in combinations(result.cameras, 2):
        both = [(views[a], views[b]) for views in seen.values() if a in views and b in views]
        if len(both) < EPIPOLAR_MIN_SHARED or a not in focal or b not in focal:
            continue
        na = np.array([pair[0] for pair in both])
        nb = np.array([pair[1] for pair in both])
        essential = _relative_essential(result, a, b)
        in_b = _line_distance(nb, na @ essential.T) * focal[b]
        in_a = _line_distance(na, nb @ essential) * focal[a]
        distance = 0.5 * (in_a + in_b)
        medians[f"{a}|{b}"] = float(np.median(distance))
        tails.append(f"{a}|{b} p90 {np.percentile(distance, 90):.2f} px ({len(both)} points)")
    if not medians:
        return Check(
            "epipolar",
            "unavailable",
            None,
            list(EPIPOLAR_MEDIAN_PX),
            "internal",
            f"no pair shares {EPIPOLAR_MIN_SHARED} observations",
        )
    worst = max(medians, key=lambda p: medians[p])
    return Check(
        "epipolar",
        _band(medians[worst], EPIPOLAR_MEDIAN_PX),
        medians[worst],
        list(EPIPOLAR_MEDIAN_PX),
        "internal",
        f"worst pair {worst}, on the solve's own observations: spots an inconsistent pair, "
        "validates nothing out of sample; " + "; ".join(tails),
        medians,
    )


def rigidity_check(result: ExtrinsicResult | None, board: CalibrationBoard) -> Check:
    """How rigid the triangulated target came out, against its longest side."""
    if result is None or result.rigidity_mm <= 0.0:
        return Check(
            "target_rigidity",
            "unavailable",
            None,
            list(RIGIDITY_SHARE),
            "internal",
            "no rigidity recorded: recompute the extrinsics",
        )
    if board.board_type is BoardType.CHARUCO:
        side = max(board.columns, board.rows) * board.square_size_mm
    else:
        side = board.marker_size_mm
    share = result.rigidity_mm / side
    return Check(
        "target_rigidity",
        _band(share, RIGIDITY_SHARE),
        share,
        list(RIGIDITY_SHARE),
        "internal",
        f"{result.rigidity_mm:.2f} mm RMS on a {side:.0f} mm target: tests whether the "
        "solve keeps the target rigid; validates neither the declared size nor the scale",
    )


def frame_checks(world: WorldFrame) -> list[Check]:
    """The world is set on a level target, printed face up, the cameras above it."""
    if world.frame != "target":
        detail = (
            "the world's origin is the anchor camera's (its axes too, unless rotated): "
            "frame it on the target on the floor"
            if world.frame == "anchor_camera"
            else f"the world's origin is {world.origin}: frame it on the target on the floor"
        )
        return [
            Check("frame", "warn", None, [], "internal", detail),
            Check(
                "cameras_above_floor",
                "unavailable",
                None,
                [],
                "internal",
                "no floor: the world is not framed on a target",
            ),
        ]
    tilt = world.tilt_deg
    if tilt is None or not world.level:
        tilted = "" if tilt is None else f" {min(tilt, 180.0 - tilt):.1f}° off horizontal"
        return [
            Check(
                "frame",
                "warn",
                tilt,
                [],
                "internal",
                f"framed on group {world.group}, but the target is no longer level{tilted}: "
                "a rotation tilted the world, frame it again, unless a wall target was "
                "turned level on purpose",
            ),
            Check(
                "cameras_above_floor",
                "unavailable",
                None,
                [],
                "internal",
                "no floor: the framed target is not level",
            ),
        ]
    if tilt > 90.0:
        posed = Check(
            "frame",
            "fail",
            tilt,
            [],
            "internal",
            f"framed on group {world.group} with its printed face down: the world is "
            "upside down, frame it again",
        )
    else:
        drift = world.target_offset_m or 0.0
        moved = (
            f"; the target sits {drift * 1000.0:.1f} mm off the origin since a refit "
            "(Minimize): framing it again puts it back, and drops a yaw applied since"
            if drift >= 0.0005
            else ""
        )
        posed = Check(
            "frame",
            "ok",
            tilt,
            [],
            "internal",
            f"framed on group {world.group}, level, printed face up; proves the target's "
            f"plane, not that it lay on the floor{moved}",
        )
    above = Check(
        "cameras_above_floor",
        "fail" if world.below else "ok",
        float(len(world.below)),
        [],
        "internal",
        f"under the floor: {', '.join(world.below)}"
        if world.below
        else "every optical centre is above the framed target",
    )
    return [posed, above]


def run_checks(
    session: CalibrationSession,
    result: ExtrinsicResult | None,
    ba: BAInputs | None,
    board: CalibrationBoard,
    world: WorldFrame,
) -> list[Check]:
    """Every check; one that cannot run reads ``unavailable``, it never blocks an export."""
    checks: list[Check] = []
    runs: list[tuple[tuple[str, ...], Callable[[], list[Check]]]] = [
        (("camera_error",), lambda: [camera_error_check(session)]),
        (("epipolar",), lambda: [epipolar_check(session, result, ba)]),
        (("target_rigidity",), lambda: [rigidity_check(result, board)]),
        (("frame", "cameras_above_floor"), lambda: frame_checks(world)),
    ]
    for ids, run in runs:
        try:
            checks.extend(run())
        except Exception as exc:  # a malformed input must not fail the export (ADR-0057)
            logger.warning("export check %s could not run: %s", "/".join(ids), exc)
            checks.extend(
                Check(i, "unavailable", None, [], "internal", f"could not run: {exc}") for i in ids
            )
    return checks
