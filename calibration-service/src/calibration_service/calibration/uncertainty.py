"""Projection uncertainty of an intrinsic solve (ADR-0055).

How far the solved model could misplace a pixel's ray, given the corner errors the
solve itself left: the intrinsic covariance propagated to each pixel, mrcal-style and
linearised. The view poses are marginalised (Schur complement), and the implied
rotation that best explains a perturbation over the covered cells is projected out
cell by cell: what a camera rotation absorbs is not an intrinsic error, since the
extrinsic solve absorbs it.

Two covariances, the larger reading kept per cell. One assumes independent corner
errors; the other is cluster-robust by view (a "sandwich"), since the errors of one view
are correlated on a real sweep (motion blur, rolling shutter, one exposure). Against
the spread of calibrations on random halves of real sweeps, the independent one reads
~2x low where the board went and the robust one matches (median ratio ~1); on
synthetic independent noise the independent one matches and the robust one, estimated
from a few dozen views, reads up to 1.6x low where the model extrapolates.

The uncertainty is euclidean: the root of the 2x2 covariance's trace (~sqrt(2) x the
per-axis sigma). Past the fold of the radial distortion (where the distorted radius
stops growing) no ray reaches a pixel: such a cell is outside the model, NaN, and
takes no part in the rotation fit.
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

# cv2.projectPoints' Jacobian columns: rvec (3), tvec (3), fx, fy, cx, cy, then the
# 5 distortion coefficients of the classic model (ADR-0032).
_POSE = slice(0, 6)
_ROTATION = slice(0, 3)
_INTRINSICS = slice(6, 15)
_UNDISTORT = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 100, 1e-12)
# Cells covered by at least this many keyframes (ADR-0039's "robust" redundancy) fit
# the implied rotation and make the "covered" summary; fewer than _MIN_FIT_CELLS such
# cells widen the fit to any coverage.
ROBUST_COVERAGE = 3
_MIN_FIT_CELLS = 10
# A cell whose undistorted ray projects back further than this from the cell centre has
# no inverse in the model (elsewhere the undistortion lands within 1e-12 px in median,
# a few hundredths near the fold).
_ROUND_TRIP_PX = 0.05


def intrinsic_covariances(
    object_points: list[NDArray[np.float32]],
    image_points: list[NDArray[np.float32]],
    matrix: NDArray[np.float64],
    distortions: NDArray[np.float64],
    rvecs: list[NDArray[np.float64]],
    tvecs: list[NDArray[np.float64]],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Covariances (9x9) of fx, fy, cx, cy, k1, k2, p1, p2, k3, view poses marginalised.

    Both share S, the Gauss-Newton normal matrix of the intrinsics with every view
    pose marginalised (Schur complement). Independent noise: ``sigma^2 S^-1``, sigma^2
    from the residuals and the degrees of freedom. Cluster-robust by view:
    ``S^-1 (sum_v g_v g_v^T) S^-1`` with g_v the view's residual projected through the
    same marginalisation, scaled by V / (V - 1) for the V views.
    """
    normal = np.zeros((9, 9))
    pose_part = np.zeros((9, 9))
    scores: list[NDArray[np.float64]] = []
    squares = 0.0
    count = 0
    for obj, img, rvec, tvec in zip(object_points, image_points, rvecs, tvecs, strict=True):
        projected, jacobian = cv2.projectPoints(obj, rvec, tvec, matrix, distortions)
        residual = (projected - img).reshape(-1)
        squares += float(residual @ residual)
        count += residual.size
        j_pose = jacobian[:, _POSE]
        j_intr = jacobian[:, _INTRINSICS]
        normal += j_intr.T @ j_intr
        cross = j_intr.T @ j_pose
        pose_inverse = np.linalg.inv(j_pose.T @ j_pose)
        pose_part += cross @ pose_inverse @ cross.T
        scores.append(j_intr.T @ residual - cross @ pose_inverse @ (j_pose.T @ residual))
    bread = np.linalg.inv(normal - pose_part)
    views = len(scores)
    dof = max(1, count - 9 - 6 * views)
    independent: NDArray[np.float64] = squares / dof * bread
    meat = sum((np.outer(g, g) for g in scores), np.zeros((9, 9))) * views / max(1, views - 1)
    robust: NDArray[np.float64] = bread @ meat @ bread
    return independent, robust


def projection_uncertainty(
    covariances: Sequence[NDArray[np.float64]],
    matrix: NDArray[np.float64],
    distortions: NDArray[np.float64],
    coverage: NDArray[np.int64],
    image_size: tuple[int, int],
) -> NDArray[np.float64]:
    """1-sigma projection uncertainty (px) at each coverage cell's centre.

    The largest reading over ``covariances``, cell by cell; NaN outside the model.
    """
    width, height = image_size
    rows, cols = coverage.shape
    xs = (np.arange(cols) + 0.5) * width / cols
    ys = (np.arange(rows) + 0.5) * height / rows
    grid_x, grid_y = np.meshgrid(xs, ys)
    pixels = np.column_stack([grid_x.ravel(), grid_y.ravel()]).reshape(-1, 1, 2)
    # R = P = None (identity, normalised output) is valid at runtime; the cv2 stub types
    # both as required, hence the ignore.
    rays = cv2.undistortPointsIter(  # type: ignore[call-overload]
        pixels, matrix, distortions, None, None, _UNDISTORT
    )
    points = np.column_stack([rays.reshape(-1, 2), np.ones(rows * cols)])
    back, jacobian = cv2.projectPoints(points, np.zeros(3), np.zeros(3), matrix, distortions)
    k1, k2, _, _, k3 = np.asarray(distortions, np.float64).ravel()[:5]
    r2 = np.sum(points[:, :2] ** 2, axis=1)
    folded = 1.0 + 3.0 * k1 * r2 + 5.0 * k2 * r2**2 + 7.0 * k3 * r2**3 <= 0.0
    missed = np.linalg.norm(back.reshape(-1, 2) - pixels.reshape(-1, 2), axis=1) > _ROUND_TRIP_PX
    valid = ~(folded | missed)
    j_rot = jacobian[:, _ROTATION].reshape(-1, 2, 3)
    j_intr = jacobian[:, _INTRINSICS].reshape(-1, 2, 9)
    fit = (coverage >= ROBUST_COVERAGE).ravel() & valid
    if fit.sum() < _MIN_FIT_CELLS:
        fit = (coverage >= 1).ravel() & valid
    rot_fit = j_rot[fit].reshape(-1, 3)
    # The rotation that best absorbs each intrinsic perturbation over the fit cells.
    implied = np.linalg.solve(rot_fit.T @ rot_fit, rot_fit.T @ j_intr[fit].reshape(-1, 9))
    residual = j_intr - j_rot @ implied
    variance = np.max(
        [np.einsum("cij,jk,cik->c", residual, cov, residual) for cov in covariances], axis=0
    )
    sigma = np.sqrt(np.maximum(variance, 0.0))
    sigma[~valid] = np.nan
    grid: NDArray[np.float64] = sigma.reshape(rows, cols)
    return grid
