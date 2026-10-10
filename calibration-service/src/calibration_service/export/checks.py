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
from calibration_service.export.camera_array import _output_size
from calibration_service.export.opencv import BASIS, WorldFrame
from calibration_service.export.reference import Reference, align, export_centres
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession
from calibration_service.site_template import SiteTemplate

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
# The world follows the reference when the best fit onto it is within this rotation
# (degrees) and translation (metres): what an applied re-alignment leaves.
REFERENCE_FOLLOWS = (0.1, 0.01)


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
    aligned = world.alignment or {}
    if world.frame == "reference" and world.group is None:
        return [
            Check(
                "frame",
                "ok",
                None,
                [],
                "internal",
                f"re-aligned on the reference {aligned.get('reference')} "
                f"({aligned.get('mode')}): its up comes from the reference, no target "
                "verifies it",
            ),
            Check(
                "cameras_above_floor",
                "unavailable",
                None,
                [],
                "internal",
                "no floor: the world is not framed on a target",
            ),
        ]
    if world.frame not in ("target", "reference"):
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
    how = (
        f"re-aligned on the reference {aligned.get('reference')} ({aligned.get('mode')}) "
        f"over the target of group {world.group}"
        if world.frame == "reference"
        else f"framed on group {world.group}"
    )
    if tilt is None or not world.level:
        remedy = (
            "the reference's floor differs from the target's: frame it again, then align "
            "in floor mode"
            if world.frame == "reference"
            else "a rotation tilted the world, frame it again, unless a wall target was "
            "turned level on purpose"
        )
        tilted = "" if tilt is None else f" {min(tilt, 180.0 - tilt):.1f}° off horizontal"
        return [
            Check(
                "frame",
                "warn",
                tilt,
                [],
                "internal",
                f"{how}, but the target is no longer level{tilted}: {remedy}",
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
            f"{how} with its printed face down: the world is upside down, frame it again",
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
            f"{how}, level, printed face up; proves the target's plane, not that it lay "
            f"on the floor{moved}",
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


def reference_check(
    session: CalibrationSession,
    result: ExtrinsicResult | None,
    board: CalibrationBoard,
    world: WorldFrame,
    reference: Reference | None,
    unreadable: str | None = None,
) -> Check:
    """Whether the world follows the deposited reference calibration (ADR-0061)."""
    if unreadable is not None:
        return Check("reference", "warn", None, [], "external", unreadable)
    if reference is None:
        return Check(
            "reference", "unavailable", None, [], "external", "no reference calibration loaded"
        )
    if result is None:
        return Check("reference", "unavailable", None, [], "external", "no extrinsic solve")
    applied = result.alignment.get("mode") if result.alignment else None
    mode = applied or ("floor" if world.up == "y" else "rigid")
    fit = align(result, session, board, reference, world, mode).report
    if fit.refused is not None:
        return Check(
            "reference",
            "warn",
            None,
            [],
            "external",
            f"{reference.name}: {fit.refused}",
        )
    rotation = fit.rotation_deg or 0.0
    moved = float(np.linalg.norm(fit.translation_m or [0.0, 0.0, 0.0]))
    residual = f"residual {100.0 * (fit.residual_rms_m or 0.0):.1f} cm RMS"
    if rotation <= REFERENCE_FOLLOWS[0] and moved <= REFERENCE_FOLLOWS[1]:
        return Check(
            "reference",
            "ok",
            fit.residual_rms_m,
            [],
            "external",
            f"the world follows {reference.name} ({mode}, matched by {fit.matched_by}), "
            f"{residual}: keeps the room's landmarks, measures nothing",
            fit.residuals_m,
        )
    return Check(
        "reference",
        "warn",
        fit.residual_rms_m,
        [],
        "external",
        f"the world is {rotation:.1f}° and {moved:.2f} m off {reference.name} "
        f"({mode}, {residual}): align it in the 3D review",
        fit.residuals_m,
    )


_TEMPLATE_IDS = ("template_binding", "template_placement", "template_scale", "template_resolution")
# Template scale bands, in standard deviations of the implicit scale (ADR-0062).
TEMPLATE_SCALE_SIGMAS = (2.0, 3.0)
_IMPORTED_PREFIX = "import:"


def _external(
    check_id: str,
    status: str,
    detail: str,
    *,
    value: float | None = None,
    thresholds: list[float] | None = None,
    items: dict[str, float] | None = None,
) -> Check:
    return Check(check_id, status, value, thresholds or [], "external", detail, items or {})


def _template_names(session: CalibrationSession, template: SiteTemplate) -> dict[str, str]:
    """Template device path -> this session's camera name. An imported session has no
    devices: its cameras are matched by the template's expected port, as a reference is
    (ADR-0061)."""
    if any(c.device_path.startswith(_IMPORTED_PREFIX) for c in session.cameras):
        by_port = {c.index: c.name for c in session.cameras}
        return {t.device_path: by_port[t.port] for t in template.cameras if t.port in by_port}
    known = {t.device_path for t in template.cameras}
    return {c.device_path: c.name for c in session.cameras if c.device_path in known}


def _binding_check(session: CalibrationSession, template: SiteTemplate) -> Check:
    if any(c.device_path.startswith(_IMPORTED_PREFIX) for c in session.cameras):
        return _external(
            "template_binding", "unavailable", "an imported session has no devices to bind"
        )
    by_device = {c.device_path: c for c in session.cameras}
    wrong: list[str] = []
    for expected in template.cameras:
        camera = by_device.get(expected.device_path)
        if camera is None:
            wrong.append(f"{expected.device_path} (port {expected.port}) is missing")
        elif camera.index != expected.port:
            wrong.append(
                f"{expected.device_path} is at port {camera.index}, expected {expected.port}"
            )
    known = {t.device_path for t in template.cameras}
    extra = sorted(c.name for c in session.cameras if c.device_path not in known)
    if wrong:
        return _external(
            "template_binding",
            "fail",
            "cable or camera order changed: " + "; ".join(wrong),
            value=float(len(wrong)),
        )
    if extra:
        return _external(
            "template_binding",
            "warn",
            f"every template camera at its port; not in the template: {', '.join(extra)}",
            value=0.0,
        )
    return _external(
        "template_binding",
        "ok",
        f"every camera of {template.name} at its expected port",
        value=0.0,
    )


def _placement(
    result: ExtrinsicResult, board: CalibrationBoard
) -> dict[str, tuple[NDArray[np.float64], float, float]]:
    """Per camera, in the export world: optical centre, pitch and yaw to the origin."""
    centres = export_centres(result, board)
    placed: dict[str, tuple[NDArray[np.float64], float, float]] = {}
    for name, centre in centres.items():
        rotation = np.asarray(cv2.Rodrigues(np.asarray(result.rotations[name]))[0], np.float64)
        axis = BASIS @ rotation.T @ np.array([0.0, 0.0, 1.0])  # optical axis, export world
        pitch = float(np.degrees(np.arcsin(np.clip(-axis[1], -1.0, 1.0))))
        flat_axis, flat_origin = axis[[0, 2]], -centre[[0, 2]]
        norms = float(np.linalg.norm(flat_axis) * np.linalg.norm(flat_origin))
        cosine = float(flat_axis @ flat_origin) / norms if norms > 0 else 1.0
        placed[name] = (centre, pitch, float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))))
    return placed


def _placement_check(
    session: CalibrationSession,
    result: ExtrinsicResult | None,
    board: CalibrationBoard,
    world: WorldFrame,
    template: SiteTemplate,
) -> Check:
    if result is None:
        return _external("template_placement", "unavailable", "no extrinsic solve")
    if world.up != "y":
        return _external(
            "template_placement",
            "unavailable",
            "the world is not posed on a level floor: bounds cannot be read",
        )
    names = _template_names(session, template)
    placed = _placement(result, board)
    outside: list[str] = []
    checked = 0
    for expected in template.cameras:
        name = names.get(expected.device_path)
        if name is None or name not in placed:
            continue
        checked += 1
        centre, pitch, yaw = placed[name]
        for axis, bounds in zip(
            "xyz",
            (expected.position_m.x, expected.position_m.y, expected.position_m.z),
            strict=True,
        ):
            value = float(centre["xyz".index(axis)])
            if bounds is not None and not bounds[0] <= value <= bounds[1]:
                outside.append(f"{name} {axis} {value:.2f} m not in [{bounds[0]}, {bounds[1]}]")
        if (
            expected.pitch_deg is not None
            and not expected.pitch_deg[0] <= pitch <= expected.pitch_deg[1]
        ):
            outside.append(
                f"{name} pitch {pitch:.1f}° not in "
                f"[{expected.pitch_deg[0]}, {expected.pitch_deg[1]}]"
            )
        if expected.yaw_to_origin_deg_max is not None and yaw > expected.yaw_to_origin_deg_max:
            outside.append(
                f"{name} yaw {yaw:.1f}° to the origin over {expected.yaw_to_origin_deg_max}"
            )
    if checked == 0:
        return _external("template_placement", "unavailable", "no template camera on this rig")
    if outside:
        return _external(
            "template_placement",
            "fail",
            "out of the site's bounds: " + "; ".join(outside),
            value=float(len(outside)),
        )
    return _external(
        "template_placement",
        "ok",
        f"{checked} cameras within the bounds of {template.name}",
        value=0.0,
    )


def _scale_check(
    session: CalibrationSession,
    result: ExtrinsicResult | None,
    board: CalibrationBoard,
    template: SiteTemplate,
) -> Check:
    """The implicit scale of the tape distances, as Caliscope v0.11.5's scaled() derives it."""
    if result is None:
        return _external("template_scale", "unavailable", "no extrinsic solve")
    if not template.distances_m:
        return _external("template_scale", "unavailable", "the template holds no distance")
    names = _template_names(session, template)
    centres = export_centres(result, board)
    cues: list[tuple[str, float, float, float]] = []
    for distance in template.distances_m:
        a, b = names.get(distance.a), names.get(distance.b)
        if a in centres and b in centres:
            length = float(np.linalg.norm(centres[a] - centres[b]))
            label = f"{a}|{b}"
            repeats = sum(1 for c in cues if c[0].split("#")[0] == label)
            cues.append(
                (
                    f"{label}#{repeats + 1}" if repeats else label,
                    length,
                    distance.m,
                    distance.sigma_m,
                )
            )
    if not cues:
        return _external(
            "template_scale", "unavailable", "no template distance joins two cameras of this rig"
        )
    solved = np.array([c[1] for c in cues])
    taped = np.array([c[2] for c in cues])
    sigma = np.array([c[3] for c in cues])
    weight = float(np.sum(solved**2 / sigma**2))
    scale = float(np.sum(taped * solved / sigma**2)) / weight
    sigma_scale = 1.0 / float(np.sqrt(weight))
    bands = (TEMPLATE_SCALE_SIGMAS[0] * sigma_scale, TEMPLATE_SCALE_SIGMAS[1] * sigma_scale)
    status = _band(abs(scale - 1.0), bands)
    implied, implied_sigma = taped / solved, sigma / solved
    disagree = [
        f"{cues[i][0]} vs {cues[j][0]}"
        for i in range(len(cues))
        for j in range(i + 1, len(cues))
        if abs(implied[i] - implied[j]) > 2.0 * float(np.hypot(implied_sigma[i], implied_sigma[j]))
    ]
    if disagree and status == "ok":
        status = "warn"
    detail = (
        f"the solve's distances need x{scale:.4f} ({100.0 * (scale - 1.0):+.2f} %, "
        f"±{100.0 * sigma_scale:.2f} % at 1 sigma) against {len(cues)} of "
        f"{len(template.distances_m)} tape distances: "
        "the scale check, which validates the declared target size"
    )
    if disagree:
        detail += "; distances disagree beyond 2 sigma: " + ", ".join(disagree)
    return _external(
        "template_scale",
        status,
        detail,
        value=scale - 1.0,
        thresholds=list(bands),
        items={c[0]: c[2] / c[1] - 1.0 for c in cues},
    )


def _resolution_check(session: CalibrationSession, template: SiteTemplate) -> Check:
    if template.resolution is None:
        return _external("template_resolution", "unavailable", "the template sets no resolution")
    expected = list(template.resolution)
    wrong = [f"{c.name} {_output_size(c)}" for c in session.cameras if _output_size(c) != expected]
    if wrong:
        return _external(
            "template_resolution",
            "fail",
            f"expected {expected[0]}x{expected[1]}: " + ", ".join(wrong),
            value=float(len(wrong)),
        )
    return _external(
        "template_resolution",
        "ok",
        f"every camera exports at {expected[0]}x{expected[1]}",
        value=0.0,
    )


def template_checks(
    session: CalibrationSession,
    result: ExtrinsicResult | None,
    board: CalibrationBoard,
    world: WorldFrame,
    template: SiteTemplate | None,
    unreadable: str | None = None,
) -> list[Check]:
    """The site template's external checks (ADR-0062), unavailable without a template."""
    if unreadable is not None:
        return [_external(i, "unavailable", unreadable) for i in _TEMPLATE_IDS]
    if template is None:
        return [
            _external(i, "unavailable", "no site template: set one in the settings")
            for i in _TEMPLATE_IDS
        ]
    return [
        _binding_check(session, template),
        _placement_check(session, result, board, world, template),
        _scale_check(session, result, board, template),
        _resolution_check(session, template),
    ]


def _scope(check_id: str) -> str:
    """What a check is judged against: the reference and the template are measured apart."""
    return "external" if check_id == "reference" or check_id in _TEMPLATE_IDS else "internal"


def run_checks(
    session: CalibrationSession,
    result: ExtrinsicResult | None,
    ba: BAInputs | None,
    board: CalibrationBoard,
    world: WorldFrame,
    reference: Reference | None = None,
    reference_unreadable: str | None = None,
    template: SiteTemplate | None = None,
    template_unreadable: str | None = None,
) -> list[Check]:
    """Every check; one that cannot run reads ``unavailable``, it never blocks an export."""
    checks: list[Check] = []
    runs: list[tuple[tuple[str, ...], Callable[[], list[Check]]]] = [
        (("camera_error",), lambda: [camera_error_check(session)]),
        (("epipolar",), lambda: [epipolar_check(session, result, ba)]),
        (("target_rigidity",), lambda: [rigidity_check(result, board)]),
        (("frame", "cameras_above_floor"), lambda: frame_checks(world)),
        (
            ("reference",),
            lambda: [
                reference_check(session, result, board, world, reference, reference_unreadable)
            ],
        ),
        (
            _TEMPLATE_IDS,
            lambda: template_checks(session, result, board, world, template, template_unreadable),
        ),
    ]
    for ids, run in runs:
        try:
            checks.extend(run())
        except Exception as exc:  # a malformed input must not fail the export (ADR-0057)
            logger.warning("export check %s could not run: %s", "/".join(ids), exc)
            checks.extend(
                Check(i, "unavailable", None, [], _scope(i), f"could not run: {exc}") for i in ids
            )
    return checks
