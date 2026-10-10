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

import numpy as np

from calibration_service.calibration.extrinsic import (
    ExtrinsicResult,
    board_object_points,
    board_unit_mm,
    camera_centres,
)
from calibration_service.models.board import CalibrationBoard
from calibration_service.models.session import CalibrationSession
from calibration_service.session.config_store import load_board_config
from calibration_service.session.store import load_session
from calibration_service.session.workflow import (
    output_scaled_errors,
    solve_extrinsics,
    sweep_settings,
)


def _load_session(directory: Path) -> CalibrationSession:
    # The service's own loader: a legacy session's matrices are migrated (ADR-0051).
    if not (directory / "session.toml").is_file():
        raise SystemExit(f"no session.toml under {directory}")
    return load_session(directory.parent, directory.name)


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None, help="also write the report as JSON")
    parser.add_argument("--stride", type=int, default=None)
    parser.add_argument("--max-groups", type=int, default=None)
    parser.add_argument(
        "--max-motion-px",
        default=None,
        help="motion gate in native px (ADR-0056), or 'off' to solve without it",
    )
    parser.add_argument("--verbose", action="store_true", help="show solver logs")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    directory: Path = args.session_dir
    session = _load_session(directory)
    _, extrinsic_board, _inherited, issues = load_board_config(directory.parent, directory.name)
    if extrinsic_board is None:
        raise SystemExit(f"no usable extrinsic board in config.toml: {'; '.join(issues)}")

    # The service's own resolution of the knobs and of the solve (session.workflow).
    gate = args.max_motion_px != "off"
    settings = sweep_settings(
        extrinsic_board,
        stride=args.stride,
        max_groups=args.max_groups,
        max_motion_px=float(args.max_motion_px) if gate and args.max_motion_px else None,
        motion_gate=gate,
    )
    max_motion_px = settings.max_motion_px
    try:
        result, ba_inputs = solve_extrinsics(
            directory / "extrinsic", session, extrinsic_board, settings
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    scaled = output_scaled_errors(result, session)
    rigidity = board_rigidity_mm(result, ba_inputs.point_corner, extrinsic_board)
    unit_mm = board_unit_mm(extrinsic_board)
    centers = {name: c * unit_mm / 1000.0 for name, c in camera_centres(result).items()}

    status = "converged" if result.ba_converged else "TRUNCATED"
    print(f"session      : {directory}")
    print(f"board        : {extrinsic_board.board_type.value}, unit {unit_mm:.1f} mm")
    print(
        f"groups/points: {result.group_count} groups, {result.point_count} points, "
        f"{result.observations_total} observations"
    )
    print(f"bundle adj.  : {status} (nfev {result.ba_nfev})")
    if max_motion_px is None:
        print("motion gate  : off")
    else:
        print(
            f"motion gate  : {result.moving_groups} detected groups dropped as moving "
            f"(> {max_motion_px} px, ADR-0056)"
        )
    if result.border_attempts:
        refused = {name: sum(c.values()) for name, c in result.border_refusals.items()}
        reasons: dict[str, int] = {}
        for counts in result.border_refusals.values():
            for reason, count in counts.items():
                reasons[reason] = reasons.get(reason, 0) + count
        print(
            f"views dropped: {sum(refused.values())} of {sum(result.border_attempts.values())}"
            " by the corner refinement, ADR-0052"
        )
        print(
            "  per camera : "
            + "  ".join(
                f"{name} {refused.get(name, 0)}/{tried}"
                for name, tried in sorted(result.border_attempts.items())
            )
        )
        if reasons:
            print("  by reason  : " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
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
            "border_refusals": result.border_refusals,
            "border_attempts": result.border_attempts,
            "moving_groups": result.moving_groups,
            "max_motion_px": max_motion_px,  # None: solved with the gate off
        }
        args.json.write_text(json.dumps(payload, indent=2))
        print(f"\nJSON report  : {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
