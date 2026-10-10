"""The generic OpenCV export target (ADR-0057): a versioned consumer contract.

``camera_array_opencv.json`` holds world -> camera poses (``x_cam = R x_world + t``,
OpenCV camera axes) in a Y-up right-handed world in metres, whatever the export
units, with the intrinsics at the export resolution, cameras sorted by port. Each
field keeps one meaning per ``version``: a reader ignores unknown fields, an addition
keeps the version, a change of meaning bumps it.

The world says where it comes from (``world.frame``) and asserts its up only when it
is verified: the framed target level, its printed face up, every camera above it.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Any

import cv2
import numpy as np

from calibration_service.calibration.extrinsic import (
    ExtrinsicResult,
    board_unit_mm,
    camera_centres,
)
from calibration_service.export.camera_array import (
    CONVENTIONS,
    _output_size,
    _rotation,
    _translation,
)
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession

FORMAT = "realtime-calib/opencv-cameras"
VERSION = 1
# The solver's world (OpenCV axes, y down) -> the contract's Y-up right-handed world:
# the three.js basis, so the two exports of one solve agree.
BASIS = np.asarray(CONVENTIONS["threejs"].basis, np.float64)
# A framed target is level when its plane is this close to horizontal.
_LEVEL_TOLERANCE_DEG = 1.0
# The anchor camera still sits at the origin (board units): a rotation about the
# origin keeps it there, a framing or an imported pose does not.
_AT_ORIGIN = 1e-6
# An imported session's device path names the uploaded file, not a device.
_IMPORTED_PREFIX = "import:"
# The solver's world has y down.
_SOLVER_UP = np.array([0.0, -1.0, 0.0])


@dataclass(frozen=True)
class WorldFrame:
    """Where the exported world comes from (ADR-0057)."""

    frame: str  # "target" (framed on a group's board) | "anchor_camera" | "unknown"
    origin: str  # human-readable
    up: str | None  # "y" when asserted (see world_frame), else None
    group: int | None = None  # the framed group
    tilt_deg: float | None = None  # the framed target's printed face against the up axis
    below: tuple[str, ...] = ()  # cameras under the framed target, when it is level
    target_offset_m: float | None = None  # the framed target's origin point off the origin

    @property
    def level(self) -> bool:
        """The framed target's plane is horizontal, whichever face is up."""
        return self.tilt_deg is not None and (
            self.tilt_deg <= _LEVEL_TOLERANCE_DEG or self.tilt_deg >= 180.0 - _LEVEL_TOLERANCE_DEG
        )


def world_frame(result: ExtrinsicResult | None, board: CalibrationBoard, anchor: str) -> WorldFrame:
    """The provenance of ``result``'s world: framed on a target, the anchor's, or unknown.

    ``up = "y"`` is asserted only when the framed target is level with its printed face
    up and every optical centre stands above it. The printed face is the corner order's
    normal for a single marker, its opposite for a ChArUco (``quad_origin_transform``).
    A Minimize refits the target, so its origin point can drift off the world's origin:
    ``target_offset_m`` reports by how much.
    """
    if result is None:
        return WorldFrame("unknown", "unknown: no readable extrinsic result", None)
    centres = camera_centres(result)
    if result.framed_group is None:
        centre = centres.get(anchor)
        if centre is None or float(np.linalg.norm(centre)) > _AT_ORIGIN:
            return WorldFrame("unknown", f"unknown: {anchor} is not at the origin", None)
        return WorldFrame("anchor_camera", f"optical centre of {anchor}", None)
    group = result.framed_group
    charuco = board.board_type is BoardType.CHARUCO
    if charuco:
        origin = f"first chessboard corner of the ChArUco of group {group}, as framed"
    else:
        origin = f"centre of the marker of group {group}, as framed"
    quad = result.board_quads[group] if group < len(result.board_quads) else None
    if quad is None:
        return WorldFrame("target", origin, None, group)
    corners = np.asarray(quad, np.float64)
    point = corners[0] if charuco else corners.mean(axis=0)
    normal = np.cross(corners[1] - corners[0], corners[3] - corners[0])
    face = (-1.0 if charuco else 1.0) * normal / np.linalg.norm(normal)
    tilt = float(np.degrees(np.arccos(np.clip(face @ _SOLVER_UP, -1.0, 1.0))))
    offset = float(np.linalg.norm(point)) * board_unit_mm(board) / 1000.0
    partial = WorldFrame("target", origin, None, group, tilt, (), offset)
    if not partial.level:
        return partial
    below = tuple(sorted(n for n, c in centres.items() if float((c - point) @ _SOLVER_UP) <= 0))
    up = "y" if tilt <= _LEVEL_TOLERANCE_DEG and not below else None
    return WorldFrame("target", origin, up, group, tilt, below, offset)


def _service_version() -> str:
    try:
        return metadata.version("calibration-service")
    except metadata.PackageNotFoundError:
        return "unknown"


def opencv_document(
    session: CalibrationSession,
    square_size_mm: float,
    world: WorldFrame,
    *,
    exported_at: str,
) -> dict[str, Any]:
    """``camera_array_opencv.json`` (ADR-0057), translations in metres."""
    cameras: list[dict[str, Any]] = []
    for camera in sorted(session.cameras, key=lambda c: c.index):
        rotation = np.asarray(cv2.Rodrigues(np.asarray(_rotation(camera)))[0], np.float64)
        translation = _translation(camera, square_size_mm, "m")
        device = camera.device_path
        cameras.append(
            {
                "port": camera.index,
                "name": camera.name,
                "device_path": None if device.startswith(_IMPORTED_PREFIX) else device,
                "resolution": _output_size(camera),
                "K": camera.matrix,
                "distCoef": camera.distortions,
                "R": [[float(v) for v in row] for row in rotation @ BASIS.T],
                "t": [[float(v)] for v in translation],
                "intrinsic_error_px": camera.calibration_error,
                "extrinsic_error_px": camera.extrinsic_error,
            }
        )
    frame: dict[str, Any] = {"frame": world.frame, "origin": world.origin}
    if world.group is not None:
        frame["group"] = world.group
    if world.up is not None:
        frame["up"] = world.up
    if world.target_offset_m is not None:
        frame["target_offset_m"] = world.target_offset_m
    return {
        "format": FORMAT,
        "version": VERSION,
        "convention": {
            "pose": "x_cam = R x_world + t",
            "camera_axes": "OpenCV: x right, y down, z forward",
            "world": "right-handed, Y up, metres",
            "basis_from_solver": [[float(v) for v in row] for row in BASIS],
            "intrinsics": (
                "K at 'resolution', mapped at pixel centres the way cv2.resize maps them"
                " onto that size"
            ),
            "distortion": ["k1", "k2", "p1", "p2", "k3"],
        },
        "world": frame,
        "source": {
            "session": session.session_id,
            "exported_at": exported_at,
            "service_version": _service_version(),
        },
        "cameras": cameras,
    }
