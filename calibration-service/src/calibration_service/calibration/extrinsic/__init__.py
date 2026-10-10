"""Extrinsic multi-camera calibration (ADR-0023, Caliscope-grounded).

Pipeline: synchronized detection groups -> pairwise ``cv2.stereoCalibrate`` on
**undistorted normalized** points (K=I, D=0, ``CALIB_FIX_INTRINSIC``) -> transform
graph with bridge-filling -> poses chained from the **anchor** (camera index 0,
identity — ADR-0012) -> **DLT triangulation** (batched SVD over all observing
cameras) -> **bundle adjustment** (``scipy.optimize.least_squares``, trf, sparse
Jacobian) refining non-anchor poses + 3D points jointly.

Two deliberate conventions (ADR-0023):
- Poses map **world (anchor frame) coords -> camera coords**: ``x_cam = R x_w + t``
  — directly usable by ``cv2.projectPoints``. Pairwise transforms map primary ->
  secondary the same way (``x_b = R_ab x_a + t_ab``), composing as
  ``T_ac = T_xc @ T_ax`` (Caliscope ``StereoPair.link``).
- The **anchor is FIXED in the BA** (its 6 params are excluded from the vector).
  Caliscope leaves every camera free (unconstrained 6-DoF gauge) and relies on the
  solver not drifting; excluding the anchor removes that gauge freedom, conditions
  the Jacobian, and guarantees ``anchor == identity`` by construction. Reprojection
  alone leaves one gauge mode — **global scale** (scaling all points + translations
  leaves every normalized projection invariant) — and it does drift in practice
  (+1.15 % on a real ChArUco sweep). The board rigidity rows (ADR-0044, extended to
  ChArUco by ADR-0046) pin it to the physical target for both board types.

Units: board squares (the ChArUco board is built with ``squareLength=1.0``);
translations stay in squares until the export scales by ``square_size_mm``
([[camera-array-config]]).

The package (EXT-11/12):

- ``model``: The extrinsic solve's data and its shared geometry helpers (ADR-0023).
- ``sweep``: A recorded sweep: sync window, groups, detection, motion gate (ADR-0007, ADR-0056).
- ``init``: Initial poses: pairwise stereo, chaining from the anchor, triangulation (ADR-0023).
- ``bundle``: The bundle adjustment, the target's rigidity, the pixel errors (ADR-0044, ADR-0046).
- ``frame``: The world frame: framing, rotations, reorientation, centres (ADR-0026, ADR-0057).
- ``pipeline``: The solve end to end: a compute from the sweep, and Minimize (ADR-0023).
"""

from __future__ import annotations

from calibration_service.calibration.extrinsic.bundle import (
    BAStatus,
    RigidityConstraints,
    build_rigidity_constraints,
    bundle_adjust,
    pixel_errors,
    rigidity_mm,
)
from calibration_service.calibration.extrinsic.frame import (
    axis_rotation_transform,
    camera_centres,
    quad_origin_transform,
    reorient_result,
)
from calibration_service.calibration.extrinsic.init import (
    Triangulation,
    chain_from_anchor,
    stereo_pairwise,
    triangulate_groups,
)
from calibration_service.calibration.extrinsic.model import (
    BAInputs,
    CameraModel,
    ExtrinsicResult,
    GroupDetection,
    PairEstimate,
    board_object_points,
    board_unit_mm,
)
from calibration_service.calibration.extrinsic.pipeline import (
    compute_extrinsic_from_sweep,
    refine_result,
)
from calibration_service.calibration.extrinsic.sweep import derive_sweep_window, sweep_groups

__all__ = [
    "BAInputs",
    "BAStatus",
    "CameraModel",
    "ExtrinsicResult",
    "GroupDetection",
    "PairEstimate",
    "RigidityConstraints",
    "Triangulation",
    "axis_rotation_transform",
    "board_object_points",
    "board_unit_mm",
    "build_rigidity_constraints",
    "bundle_adjust",
    "camera_centres",
    "chain_from_anchor",
    "compute_extrinsic_from_sweep",
    "derive_sweep_window",
    "pixel_errors",
    "quad_origin_transform",
    "refine_result",
    "reorient_result",
    "rigidity_mm",
    "stereo_pairwise",
    "sweep_groups",
    "triangulate_groups",
]
