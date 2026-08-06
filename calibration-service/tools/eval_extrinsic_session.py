"""Offline extrinsic quality report for a recorded session (no service, no GUI).

Re-solves the persisted sweep with the CURRENT code and prints the metrics that
actually characterise an array solve:

* reprojection RMSE, native and at the operator's output resolution (ADR-0042),
* per-camera RMSE and residual percentiles,
* **board rigidity**: how far the triangulated corners deviate from the physical
  target (mm) — the reprojection-independent judge. A bundle adjustment can
  always trade board deformation for a lower RMSE, so this is the number that
  catches a solve which looks good and is not,
* inter-camera distances, for comparison against a tape measure or another tool.

Usage (from calibration-service/):

    uv run python tools/eval_extrinsic_session.py <session_dir> [--json out.json]

``<session_dir>`` is a session folder holding ``session.toml``, ``config.toml``
and ``extrinsic/`` (videos + timestamp sidecars) — exactly what the service
writes. Solve knobs default to the same TUNING values the API resolves.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import rtoml
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic import (
    CameraModel,
    ExtrinsicResult,
    board_object_points,
    board_unit_mm,
    compute_extrinsic_from_sweep,
    derive_sweep_window,
)
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.session.config_store import load_board_config
from calibration_service.tuning import TUNING


def _load_session(directory: Path) -> dict[str, object]:
    path = directory / "session.toml"
    if not path.is_file():
        raise SystemExit(f"no session.toml under {directory}")
    return rtoml.load(path.read_text())


def _camera_models(session: dict) -> tuple[list[CameraModel], dict[str, float]]:
    """Solver intrinsics at the RECORDING resolution + each camera's resize factor."""
    models: list[CameraModel] = []
    factors: dict[str, float] = {}
    for camera in session["cameras"]:
        if camera.get("matrix") is None:
            raise SystemExit(f"{camera['name']} has no intrinsics; calibrate it first")
        factor = float(camera.get("resize_factor") or 1.0)
        matrix = np.asarray(camera["matrix"], np.float64).copy()
        matrix[0] /= factor
        matrix[1] /= factor
        models.append(
            CameraModel(
                name=camera["name"],
                matrix=matrix,
                distortions=np.asarray(camera["distortions"], np.float64),
            )
        )
        factors[camera["name"]] = factor
    return models, factors


def board_rigidity_mm(
    result: ExtrinsicResult, point_corner: list[int], board: CalibrationBoard
) -> dict[str, float]:
    """Deviation of the triangulated corners from the physical board, in mm.

    Per group, every pair of reconstructed corners is compared against the
    distance the board's geometry mandates (``board_object_points`` scaled by the
    physical unit). Scale-sensitive by construction: a solve whose world is 2%
    too large shows up here even with a perfect RMSE.
    """
    reference = board_object_points(board) * board_unit_mm(board)
    points = np.asarray(result.points, np.float64) * board_unit_mm(board)
    groups = np.asarray(result.point_groups, np.intp)
    corners = np.asarray(point_corner, np.int32)
    deviations: list[float] = []
    for group in np.unique(groups):
        members = np.flatnonzero(groups == group)
        if len(members) < 2:
            continue
        ids = corners[members]
        world = points[members]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                expected = float(np.linalg.norm(reference[ids[i]] - reference[ids[j]]))
                measured = float(np.linalg.norm(world[i] - world[j]))
                deviations.append(measured - expected)
    if not deviations:
        return {"pairs": 0.0, "rms_mm": float("nan"), "p95_abs_mm": float("nan")}
    array = np.asarray(deviations, np.float64)
    return {
        "pairs": float(len(array)),
        "rms_mm": float(np.sqrt(np.mean(array**2))),
        "mean_abs_mm": float(np.mean(np.abs(array))),
        "p95_abs_mm": float(np.percentile(np.abs(array), 95)),
    }


def camera_centers(result: ExtrinsicResult, unit_mm: float) -> dict[str, NDArray[np.float64]]:
    """Camera positions in world coords (metres), from the world->cam poses."""
    centers: dict[str, NDArray[np.float64]] = {}
    for name in result.cameras:
        rotation = np.asarray(cv2.Rodrigues(np.asarray(result.rotations[name]))[0], np.float64)
        translation = np.asarray(result.translations[name], np.float64)
        centers[name] = (-rotation.T @ translation) * unit_mm / 1000.0
    return centers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None, help="also write the report as JSON")
    parser.add_argument("--stride", type=int, default=None)
    parser.add_argument("--max-groups", type=int, default=None)
    parser.add_argument("--verbose", action="store_true", help="show solver logs")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    directory: Path = args.session_dir
    session = _load_session(directory)
    _, extrinsic_board, issues = load_board_config(directory.parent, directory.name)
    if extrinsic_board is None:
        raise SystemExit(f"no usable extrinsic board in config.toml: {'; '.join(issues)}")

    models, factors = _camera_models(session)
    names = [model.name for model in models]
    sweep = directory / "extrinsic"
    charuco = extrinsic_board.board_type is BoardType.CHARUCO
    stride = args.stride or (
        TUNING.extrinsic_stride_charuco if charuco else TUNING.extrinsic_stride_marker
    )
    max_groups = args.max_groups or (
        TUNING.max_groups_charuco if charuco else TUNING.max_groups_marker
    )

    window_s = derive_sweep_window(sweep, names)
    result, ba_inputs = compute_extrinsic_from_sweep(
        sweep,
        extrinsic_board,
        models,
        anchor=names[0],
        window_s=window_s,
        stride=stride,
        max_groups=max_groups,
        min_shared=TUNING.min_shared,
    )
    scaled = result.scaled_errors(factors)
    rigidity = board_rigidity_mm(result, ba_inputs.point_corner, extrinsic_board)
    centers = camera_centers(result, board_unit_mm(extrinsic_board))

    unit_mm = board_unit_mm(extrinsic_board)
    status = "converged" if result.ba_converged else "TRUNCATED"
    print(f"session      : {directory}")
    print(f"board        : {extrinsic_board.board_type.value}, unit {unit_mm:.1f} mm")
    print(
        f"groups/points: {result.group_count} groups, {result.point_count} points, "
        f"{result.observations_total} observations"
    )
    print(f"bundle adj.  : {status} (nfev {result.ba_nfev})")
    print()
    print(f"RMSE native  : {result.error:.3f} px")
    print(f"RMSE output  : {scaled.error:.3f} px  (ADR-0042 reporting contract)")
    print(
        "per camera   : "
        + "  ".join(f"{name} {scaled.per_camera_error[name]:.3f}" for name in result.cameras)
    )
    print()
    print(
        f"rigidity     : {rigidity['rms_mm']:.2f} mm RMS, p95 {rigidity['p95_abs_mm']:.2f} mm "
        f"({int(rigidity['pairs'])} corner pairs)"
    )
    print(
        "distances (m): "
        + "  ".join(
            f"{a}|{b} {np.linalg.norm(centers[a] - centers[b]):.3f}"
            for i, a in enumerate(result.cameras)
            for b in result.cameras[i + 1 :]
        )
    )

    if args.json is not None:
        payload = {
            "rmse_native_px": result.error,
            "rmse_output_px": scaled.error,
            "per_camera_output_px": scaled.per_camera_error,
            "rigidity": rigidity,
            "camera_centers_m": {name: center.tolist() for name, center in centers.items()},
            "group_count": result.group_count,
            "point_count": result.point_count,
            "observations": result.observations_total,
            "ba_converged": result.ba_converged,
        }
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"\nJSON report  : {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
