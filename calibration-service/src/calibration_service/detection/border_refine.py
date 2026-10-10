"""Unbiased single-ArUco corners from the marker's own black border (ADR-0052).

Why: every edge-based corner locator (OpenCV CORNER_REFINE_CONTOUR included) sees an edge
displaced toward its DARK side by some delta: optics and motion blur followed by the camera's
gamma encoding, sharpening and thresholding all push the apparent light/dark boundary into
the dark region. For a black marker on white paper every outer edge moves inward, so the
marker looks ~2 % too small and a calibration scaled by it ~2 % too large.

How: the black border is exactly one cell wide. Where the adjacent data cell is white, the
border also has an INNER edge (dark -> light going inward), displaced toward its own dark
side - outward - by the same delta. The outer edges, moved outward by delta, define the true
marker quad and hence the homography H (marker cells -> image); H predicts exactly where each
inner border edge truly is (the image of the line one cell inward), under any perspective.
delta is the value that puts the observed inner-edge points delta outward of that prediction:
linear to first order, solved by Gauss-Newton.

A fronto-parallel model (true border width = L/6 at mid-side) is wrong under foreshortening:
the near side's border is wider and the far side's narrower, which biases delta by opposite
amounts on opposite sides (~1 px per side at 40 deg). This model removes that error and is
robust to real footage:

* a geometry pre-pass on the outer edges alone, so the full pass samples from a quad within
  ~delta of the truth even when the seed is several pixels off (CONTOUR is, on large sharp
  markers, under MJPEG smears and under occlusion);
* profiles sampled in marker coordinates through H (they land in the intended cells under
  any perspective), each scaled to the local cell size along its own ray, with plateau
  windows 0.35-0.7 cell from the edge (sharpening halos bias levels measured closer);
* per-profile checks: inside the image, contrast against the marker's typical contrast, one
  clean light -> dark crossing near the predicted edge;
* Tukey-biweight IRLS line fits started from the predicted direction and the median offset
  (a finger over part of an edge cannot drag the first fit);
* one delta per side: on hand-held sweeps the near side of a tilted marker is measurably
  blurrier than the far side (motion blur and defocus scale with depth), so opposite sides
  do not share delta; a side with no usable inner-edge point follows its opposite side;
* the edges between data bits join automatically when a pair of opposite sides has fewer
  than 2 white border-adjacent cells (ids 16, 17, 56 and 62 of DICT_4X4_100), their delta
  interpolated from the two parallel sides;
* refusal with a typed reason instead of a wrong answer, inside the validated domain.

Domain: a white margin of at least ~0.75 cell around the marker (the repo's printable
rendering has 0.9 cell, the physical target 1.1), and cells at least ~6 times the blur's
sigma. Outside it the levels beside the outer edges are not flat - the dark background
enters the light plateau, or the blur reaches the plateau windows - and the guard
(``plateau_not_flat``) refuses the view, over all four sides and side by side. On
synthetic views it refuses every uniform margin of 0.6 cell or less and every one-sided
margin of 0.5 cell or less (narrower margins that pass, 0.65-0.7 cell all round or
0.55-0.7 cell on one side, stay within 0.07 px), but only 88 % of the views with cells
under 6 blur sigmas (all of them under 4): the 12 it lets through came out 0.16 px off
in median, 1.5 px at p95 and 2.4 px at worst, against 3.3-3.5 px in median for CONTOUR.

Conventions: pixel-centre coordinates (OpenCV's); corners ordered TL, TR, BR, BL as returned
by cv2.aruco; marker cell coordinates (u, v) in [0, N]^2 (N = data bits per side + 2), u to
the right, v down, the data bits in [1, N - 1]^2. Side i joins corner i to corner i + 1 (top,
right, bottom, left). Call it on the same gray image the detector saw (already inverted for
an inverted target).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import lru_cache
from typing import NamedTuple

import cv2
import numpy as np
from numpy.typing import NDArray

F64 = NDArray[np.float64]

_NEXT = np.array([1, 2, 3, 0])  # corner / side i + 1
_PREV = np.array([3, 0, 1, 2])  # side i - 1
# one cell inward from side i, in marker coordinates
_INWARD = np.array([[0.0, 1.0], [-1.0, 0.0], [0.0, -1.0], [1.0, 0.0]])
_TUKEY_C = 4.685


class BorderRefusal(StrEnum):
    """Why a view was not refined (the caller drops the observation)."""

    MARKER_TOO_SMALL = "marker_too_small"  # cells below min_cell_px: plateaus not resolved
    DEGENERATE_QUAD = "degenerate_quad"  # seed not convex, or near-parallel adjacent edges
    OUTER_EDGE_MISSING = "outer_edge_missing"  # too few valid outer-edge profiles on a side
    OUTER_EDGE_NOISY = "outer_edge_noisy"  # one side much noisier (occlusion, MJPEG smear)
    INNER_EDGE_MISSING = "inner_edge_missing"  # no side with enough inner-edge points
    INNER_EDGE_NOISY = "inner_edge_noisy"  # inner-edge points too scattered to observe delta
    DELTA_IMPLAUSIBLE = "delta_implausible"  # |delta| or its spread over the sides too large
    PLATEAU_NOT_FLAT = "plateau_not_flat"  # out of the domain: blur too wide, margin too narrow
    NUMERICAL_FAILURE = "numerical_failure"  # a non-finite intermediate, or an OpenCV/numpy error


@dataclass(frozen=True)
class BorderParams:
    """Tuning of :func:`refine_marker_corners`.

    The defaults were validated on synthetic views with ground truth (tilt <= 65 deg,
    motion blur <= 12 px, gamma 2.2, sharpening, JPEG) and on real MJPEG footage (ChArUco
    ground truth, static frames, end-to-end extrinsic solves), inside the domain of the
    module docstring.
    """

    # also the edges between data bits; None = only when a pair of opposite sides has < 2
    # white adjacent cells (that pair's delta is otherwise unobservable and anisotropic blur
    # biases it: 0.43 px RMS for id 17 under 6 px motion blur, 0.05 px with them)
    use_bit_edges: bool | None = None
    samples_per_cell: float = 6.0  # profile density along the edges
    outer_margin: float = 0.5  # outer-edge samples stay this far (cells) from the corners
    inner_end_margin: float = 0.25  # inner samples stay this far (cells) from bit transitions
    reach: float = 0.75  # profile half-length (cells) on each side of the predicted edge
    plateau: tuple[float, float] = (0.35, 0.7)  # light / dark level windows (cells from edge)
    search_seed: float = 0.45  # crossing search window around the seed's edges (cells)
    search: float = 0.35  # crossing search window in the full pass (cells)
    step_px: float = 0.25  # profile sampling step (upper bound, px)
    min_contrast: float = 12.0  # gray levels, absolute floor
    rel_contrast: float = 0.4  # x the median profile contrast of the marker
    min_cell_px: float = 4.0  # below this the plateaus are not resolved
    min_outer_fraction: float = 0.5  # valid inliers required per side, of the planned samples
    min_inner_points: int = 4  # per side, to observe its own delta
    max_outer_scale_px: float = 3.0  # robust scatter of any side's outer-edge inliers (px)
    max_outer_scale_ratio: float = 4.0  # worst side's scatter vs the median side's ...
    outer_scale_floor_px: float = 1.0  # ... checked only above this scatter (px)
    max_inner_scale_px: float = 1.5  # an edge group noisier than this is not used (px)
    max_delta_cells: float = 0.2  # |delta| bound (cells) ...
    max_delta_px: float = 3.0  # ... and (px)
    max_delta_spread_px: float = 1.5  # max - min of the per-side deltas (px)
    # median over the outer edges of the near (0.35-0.53 cell) vs far (0.53-0.7 cell)
    # level gap beside an edge, over the contrast: 0.013-0.032 on real sweeps. At 0.08
    # it refuses every uniform synthetic margin of 0.6 cell or less and 88 % of the
    # views with cells under 6 blur sigmas, for ~1 % more refusals on real footage
    max_plateau_slope: float = 0.08
    # the same gap on the worst side alone (median of its outer profiles): a margin
    # narrowed along one side only, which the four-side median misses. At 0.2 it refuses
    # every one-sided margin of 0.5 cell or less, for 0-0.4 % more real refusals
    max_side_plateau_slope: float = 0.2


@dataclass(frozen=True, eq=False)
class BorderRefinement:
    """Refined corners and their diagnostics."""

    corners: NDArray[np.float32]  # (4, 2) TL, TR, BR, BL, pixel-centre coords
    delta: F64  # (4,) per side (px); + = edges seen inside their dark side
    outer_scale: F64  # (4,) robust scatter of the outer-edge inliers (px)
    inner_scale: F64  # (4,) robust scatter of the inner border edge residuals (px, nan: none)
    n_outer: NDArray[np.int64]  # (4,) outer-edge inliers per side
    n_inner: NDArray[np.int64]  # (4,) edge points constraining each side's delta
    cell_px: float  # mean cell size (px)


class _Refused(Exception):
    """Internal control flow: the view is unreliable."""

    def __init__(self, reason: BorderRefusal) -> None:
        super().__init__(reason)
        self.reason = reason


def marker_bits(dictionary: cv2.aruco.Dictionary, marker_id: int) -> NDArray[np.bool_]:
    """The n x n data bits of a dictionary marker (True = white), rows top to bottom."""
    n = int(dictionary.markerSize)
    image = np.asarray(dictionary.generateImageMarker(marker_id, n + 2, borderBits=1))
    return np.ascontiguousarray((image > 127)[1:-1, 1:-1])


@dataclass(frozen=True, eq=False)
class _Plan:
    """Edge sites in marker coordinates; depends on the bits and the sampling params only."""

    cells: int  # N, cells per side including the border
    grid: NDArray[np.float32]  # (4, 2) marker-space corners for getPerspectiveTransform
    point: F64  # (n, 2) site on its line
    line: NDArray[np.int64]  # (n,) index in the line table
    dark: F64  # (n,) +1: dark side on the line's positive-normal side, -1 otherwise
    outer: NDArray[np.bool_]  # (n,) outer-edge site (defines the quad)
    group: NDArray[np.int64]  # (n,) 0-3: side (border edges); 4 / 5: horizontal / vertical bits
    dweight: F64  # (n, 4): site delta = dweight @ per-side delta
    line_ends: F64  # (k, 2, 2) two marker points per line
    line_normal: F64  # (k, 2) marker-space unit normal (positive side)
    outer_per_side: int  # outer sites per side (side-major order, sides 0..3)


class _Sites:
    """Accumulator of edge sites while building a plan."""

    def __init__(self) -> None:
        self.point: list[F64] = []
        self.line: list[int] = []
        self.dark: list[float] = []
        self.outer: list[bool] = []
        self.group: list[int] = []
        self.dweight: list[F64] = []

    def add(
        self,
        ts: F64,
        base: F64,
        direction: F64,
        line: int,
        dark: float,
        outer: bool,
        group: int,
        dweight: F64,
    ) -> None:
        for t in ts:
            self.point.append(base + t * direction)
            self.line.append(line)
            self.dark.append(dark)
            self.outer.append(outer)
            self.group.append(group)
            self.dweight.append(dweight)


def _adjacent_bits(bits: NDArray[np.bool_], side: int) -> NDArray[np.bool_]:
    """Data bits along a side, in its direction of travel."""
    return (bits[0, :], bits[:, -1], bits[-1, ::-1], bits[::-1, 0])[side]


@lru_cache(maxsize=64)
def _plan(
    bits_key: bytes,
    n_bits: int,
    samples_per_cell: float,
    outer_margin: float,
    end_margin: float,
    use_bit_edges: bool,
) -> _Plan:
    bits = np.frombuffer(bits_key, dtype=np.bool_).reshape(n_bits, n_bits)
    n_cells = n_bits + 2
    grid = np.array([[0.0, 0.0], [n_cells, 0.0], [n_cells, n_cells], [0.0, n_cells]])
    tangent = (grid[_NEXT] - grid) / n_cells
    step = 1.0 / samples_per_cell
    # line table: 0-3 outer edges, 4-7 inner border edges (normals inward), then the internal
    # horizontal lines v = 2 .. N-2 and vertical lines u = 2 .. N-2 (normals +v / +u)
    ends: list[list[F64]] = []
    normals: list[F64] = []
    for s in range(4):
        ends.append([grid[s], grid[_NEXT[s]]])
        normals.append(_INWARD[s])
    for s in range(4):
        ends.append([grid[s] + _INWARD[s], grid[_NEXT[s]] + _INWARD[s]])
        normals.append(_INWARD[s])
    for k in range(2, n_cells - 1):
        ends.append([np.array([0.0, k]), np.array([float(n_cells), k])])
        normals.append(np.array([0.0, 1.0]))
    for k in range(2, n_cells - 1):
        ends.append([np.array([k, 0.0]), np.array([k, float(n_cells)])])
        normals.append(np.array([1.0, 0.0]))
    first_h, first_v = 8, 8 + (n_cells - 3)
    sites = _Sites()
    unit = np.eye(4)
    t_out = np.arange(outer_margin, n_cells - outer_margin + 1e-9, step)
    for s in range(4):
        sites.add(t_out, grid[s], tangent[s], s, 1.0, True, s, unit[s])
    for s in range(4):
        # the inner border edge exists along runs of white adjacent bits; its dark side is out
        adjacent = _adjacent_bits(bits, s)
        j = 0
        while j < n_bits:
            if not adjacent[j]:
                j += 1
                continue
            k = j
            while k + 1 < n_bits and adjacent[k + 1]:
                k += 1
            ts = np.arange(1 + j + end_margin, 2 + k - end_margin + 1e-9, step)
            sites.add(ts, grid[s] + _INWARD[s], tangent[s], 4 + s, -1.0, False, s, unit[s])
            j = k + 1
    if use_bit_edges:
        t_cell = np.arange(end_margin, 1 - end_margin + 1e-9, step)
        along_u, along_v = np.array([1.0, 0.0]), np.array([0.0, 1.0])
        for r in range(n_bits - 1):  # between bit rows r and r + 1: line v = 2 + r
            v = 2.0 + r
            weight = np.array([1 - v / n_cells, 0.0, v / n_cells, 0.0])  # top .. bottom
            for c in range(n_bits):
                if bits[r, c] != bits[r + 1, c]:
                    dark = 1.0 if not bits[r + 1, c] else -1.0
                    base = np.array([1.0 + c, v])
                    sites.add(t_cell, base, along_u, first_h + r, dark, False, 4, weight)
        for c in range(n_bits - 1):  # between bit columns c and c + 1: line u = 2 + c
            u = 2.0 + c
            weight = np.array([0.0, u / n_cells, 0.0, 1 - u / n_cells])  # left .. right
            for r in range(n_bits):
                if bits[r, c] != bits[r, c + 1]:
                    dark = 1.0 if not bits[r, c + 1] else -1.0
                    base = np.array([u, 1.0 + r])
                    sites.add(t_cell, base, along_v, first_v + c, dark, False, 5, weight)
    return _Plan(
        cells=n_cells,
        grid=grid.astype(np.float32),
        point=np.array(sites.point, np.float64),
        line=np.array(sites.line, np.int64),
        dark=np.array(sites.dark, np.float64),
        outer=np.array(sites.outer, bool),
        group=np.array(sites.group, np.int64),
        dweight=np.array(sites.dweight, np.float64),
        line_ends=np.array(ends, np.float64),
        line_normal=np.array(normals, np.float64),
        outer_per_side=int(t_out.size),
    )


def _homography(plan: _Plan, corners: F64) -> F64:
    matrix = np.asarray(cv2.getPerspectiveTransform(plan.grid, corners.astype(np.float32)))
    if not np.isfinite(matrix).all():
        raise _Refused(BorderRefusal.NUMERICAL_FAILURE)
    return matrix.astype(np.float64)


def _apply(H: F64, pts: F64) -> F64:
    q = pts @ H[:, :2].T + H[:, 2]
    return np.asarray(q[..., :2] / q[..., 2:3], np.float64)


def _image_lines(H: F64, ends: F64, normals: F64) -> tuple[F64, F64]:
    """Image lines n.x = rho of marker lines, n oriented toward each line's positive side."""
    e = _apply(H, ends.reshape(-1, 2)).reshape(-1, 2, 2)
    ref = _apply(H, ends.mean(axis=1) + 0.5 * normals)
    a, b = e[:, 0], e[:, 1]
    nx = a[:, 1] - b[:, 1]
    ny = b[:, 0] - a[:, 0]
    toward = nx * (ref[:, 0] - a[:, 0]) + ny * (ref[:, 1] - a[:, 1])
    k = np.where(toward < 0, -1.0, 1.0) / np.sqrt(nx * nx + ny * ny)
    n = np.empty((len(a), 2))
    n[:, 0] = nx * k
    n[:, 1] = ny * k
    return n, n[:, 0] * a[:, 0] + n[:, 1] * a[:, 1]


def _intersect(n: F64, rho: F64) -> F64:
    """Corner i = line (i - 1) x line i (Cramer's rule)."""
    n1, r1 = n[_PREV], rho[_PREV]
    det = n1[:, 0] * n[:, 1] - n1[:, 1] * n[:, 0]
    if np.any(np.abs(det) < 1e-6):
        raise _Refused(BorderRefusal.DEGENERATE_QUAD)
    out = np.empty((4, 2))
    out[:, 0] = (r1 * n[:, 1] - rho * n1[:, 1]) / det
    out[:, 1] = (n1[:, 0] * rho - n[:, 0] * r1) / det
    return out


def _tukey(u: F64) -> F64:
    w = 1.0 - u * u
    w[w < 0] = 0.0
    return w * w


def _median(x: F64) -> float:
    """Median of a small 1-D array (np.median's overhead dominates at this size)."""
    s = np.sort(x)
    n = s.size
    return float(s[n // 2]) if n % 2 else 0.5 * float(s[n // 2 - 1] + s[n // 2])


def _mad_scale(r: F64, floor: float) -> float:
    return max(1.4826 * _median(np.abs(r - _median(r))), floor)


def _masked_median(x: F64, mask: NDArray[np.bool_]) -> F64:
    """Row-wise median of x over mask."""
    s = np.sort(np.where(mask, x, np.inf), axis=1)
    k = mask.sum(axis=1)
    rows = np.arange(len(x))
    return np.asarray(0.5 * (s[rows, np.maximum(k - 1, 0) // 2] + s[rows, k // 2]), np.float64)


class _LineFit(NamedTuple):
    normal: F64  # (4, 2) unit normals toward the marker centre
    rho: F64  # (4,) lines normal . x = rho
    weight: F64  # (4, n) final Tukey weights
    scale: F64  # (4,) robust scatter of the inliers (px)


def _fit_lines(
    P: F64, valid: NDArray[np.bool_], centre: F64, n0: F64, iters: int = 3, floor: float = 0.05
) -> _LineFit:
    """Tukey IRLS total-least-squares lines, one per row of P (4, n, 2).

    Robust start: the predicted directions ``n0`` (4, 2) and the median offset of the points.
    """
    px, py = P[..., 0], P[..., 1]
    r = px * n0[:, 0:1] + py * n0[:, 1:2]
    r = r - _masked_median(r, valid)[:, None]
    included = valid
    n = n0.copy()
    rho = np.zeros(len(P))
    w = valid.astype(np.float64)
    for _ in range(iters + 1):
        med = _masked_median(r, included)
        scale = np.maximum(1.4826 * _masked_median(np.abs(r - med[:, None]), included), floor)
        u = (r - med[:, None]) / (_TUKEY_C * scale[:, None])
        w = np.where(valid, np.clip(1.0 - u * u, 0.0, None) ** 2, 0.0)
        included = w > 0
        sw = w.sum(axis=1)
        if np.any(sw < 3):
            raise _Refused(BorderRefusal.OUTER_EDGE_NOISY)
        mx = (w * px).sum(axis=1) / sw
        my = (w * py).sum(axis=1) / sw
        qx = px - mx[:, None]
        qy = py - my[:, None]
        wqx = w * qx
        sxy = (wqx * qy).sum(axis=1)
        theta = 0.5 * np.arctan2(2 * sxy, (wqx * qx).sum(axis=1) - (w * qy * qy).sum(axis=1))
        nx, ny = -np.sin(theta), np.cos(theta)  # normal of the direction of largest spread
        k = np.where(nx * (centre[0] - mx) + ny * (centre[1] - my) < 0, -1.0, 1.0)
        nx, ny = nx * k, ny * k
        rho = nx * mx + ny * my
        r = px * nx[:, None] + py * ny[:, None] - rho[:, None]
        n = np.stack([nx, ny], axis=1)
    dev = np.abs(r - _masked_median(r, included)[:, None])
    scale = np.maximum(1.4826 * _masked_median(dev, included), floor)
    return _LineFit(n, rho, w, scale)


def _delta_basis(observable: NDArray[np.bool_]) -> F64:
    """M (4 x p) with delta = M @ theta: one free delta per observable side.

    An unobservable side (no usable edge point) follows its opposite side when that one is
    observable (same blur direction), else the mean of the observable sides.
    """
    sides = np.flatnonzero(observable)
    M = np.zeros((4, len(sides)))
    M[sides, np.arange(len(sides))] = 1.0
    for s in np.flatnonzero(~observable):
        opposite = (s + 2) % 4
        M[s] = M[opposite] if observable[opposite] else M[sides].mean(axis=0)
    return M


class _EdgeModel:
    """Residuals, toward each point's dark side, of the non-outer edge points for a per-side
    delta: zero when every edge is observed exactly delta inside its dark side of where the
    homography of the delta-corrected outer quad puts it."""

    def __init__(
        self,
        plan: _Plan,
        n_out: F64,
        rho_out: F64,
        pts: F64,
        line: NDArray[np.int64],
        dark: F64,
        dweight: F64,
    ) -> None:
        self.plan, self.n_out, self.rho_out = plan, n_out, rho_out
        self.pts, self.dark, self.dweight = pts, dark, dweight
        self.lines = np.unique(line)
        self.index = np.searchsorted(self.lines, line)

    def corners(self, delta: F64) -> F64:
        return _intersect(self.n_out, self.rho_out - delta)  # true outer edges: delta outward

    def residuals(self, delta: F64) -> F64:
        H = _homography(self.plan, self.corners(delta))
        ends = self.plan.line_ends[self.lines]
        n, rho = _image_lines(H, ends, self.plan.line_normal[self.lines])
        nn, rr = n[self.index], rho[self.index]
        offset = (self.pts * nn).sum(axis=1) - rr
        return np.asarray(self.dark * offset - self.dweight @ delta, np.float64)


def _solve_delta(model: _EdgeModel, weights: F64, M: F64) -> F64:
    """Chord Gauss-Newton (linear to first order): one finite-difference Jacobian, 2 steps."""
    sw = np.sqrt(weights)
    p = M.shape[1]
    theta = np.zeros(p)
    r0 = model.residuals(M @ theta)
    eps = 0.1
    J = np.empty((len(r0), p))
    for k in range(p):
        th = theta.copy()
        th[k] += eps
        J[:, k] = (model.residuals(M @ th) - r0) / eps
    A = J * sw[:, None]
    normal = A.T @ A
    if not np.isfinite(normal).all():
        raise _Refused(BorderRefusal.NUMERICAL_FAILURE)
    if np.linalg.cond(normal) > 1e8:
        raise _Refused(BorderRefusal.INNER_EDGE_MISSING)
    r = r0
    for _ in range(2):
        theta = theta - np.linalg.solve(normal, A.T @ (r * sw))
        r = model.residuals(M @ theta)
    return np.asarray(M @ theta, np.float64)


class _Profiles(NamedTuple):
    base: F64  # (n, 2) site in the image
    normal: F64  # (n, 2) image unit normal of the site's line, toward its dark side
    cell: F64  # (n,) local cell size along the ray (px)
    kappa: F64  # (k,) sample offsets (cells), + = dark side
    values: NDArray[np.float32]  # (n, k) intensity profiles, light -> dark
    inside: NDArray[np.bool_]  # (n,) the whole profile lies inside the image


def _profiles(
    g: NDArray[np.float32],
    H: F64,
    plan: _Plan,
    sel: NDArray[np.bool_],
    p: BorderParams,
    width: int,
    height: int,
) -> _Profiles:
    """Intensity profiles across the selected sites' edges, in cells of their own ray."""
    pts = plan.point[sel]
    n_line, _ = _image_lines(H, plan.line_ends, plan.line_normal)
    toward_dark = plan.dark[sel][:, None]
    nrm = n_line[plan.line[sel]] * toward_dark
    base = _apply(H, pts)
    dm = plan.line_normal[plan.line[sel]] * toward_dark
    cell = np.abs(((_apply(H, pts + 0.5 * dm) - _apply(H, pts - 0.5 * dm)) * nrm).sum(axis=1))
    if not (np.isfinite(cell).all() and np.isfinite(base).all() and np.isfinite(nrm).all()):
        raise _Refused(BorderRefusal.NUMERICAL_FAILURE)
    nk = int(np.clip(math.ceil(2 * p.reach * float(cell.max()) / p.step_px) + 1, 41, 801))
    kappa = np.linspace(-p.reach, p.reach, nk)
    off = cell[:, None] * kappa[None, :]
    xs = (base[:, 0:1] + off * nrm[:, 0:1]).astype(np.float32)
    ys = (base[:, 1:2] + off * nrm[:, 1:2]).astype(np.float32)
    inside = (xs.min(axis=1) >= 0.0) & (xs.max(axis=1) <= width - 1.0)
    inside &= (ys.min(axis=1) >= 0.0) & (ys.max(axis=1) <= height - 1.0)
    sampled = cv2.remap(g, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return _Profiles(base, nrm, cell, kappa, np.asarray(sampled, np.float32), inside)


def _crossings(
    prof: _Profiles, p: BorderParams, search: float
) -> tuple[F64, NDArray[np.bool_], F64]:
    """Each profile's sub-sample 50 % light -> dark crossing (kappa), validity, plateau slope.

    The slope is the larger gap, light or dark side, between the levels near the edge
    (0.35-0.53 cell) and far from it (0.53-0.7 cell), over the contrast: a plateau the blur
    or the background reaches is not flat.
    """
    v, kappa = prof.values, prof.kappa
    lo, hi = p.plateau
    mid = 0.5 * (lo + hi)
    near_light, far_light = (kappa >= -mid) & (kappa <= -lo), (kappa >= -hi) & (kappa < -mid)
    near_dark, far_dark = (kappa >= lo) & (kappa <= mid), (kappa > mid) & (kappa <= hi)
    light = np.median(v[:, near_light | far_light], axis=1)
    dark = np.median(v[:, near_dark | far_dark], axis=1)
    contrast = light - dark
    gap = np.maximum(
        np.abs(np.median(v[:, near_light], axis=1) - np.median(v[:, far_light], axis=1)),
        np.abs(np.median(v[:, near_dark], axis=1) - np.median(v[:, far_dark], axis=1)),
    )
    slope = np.asarray(gap / np.maximum(contrast, 1e-6), np.float64)
    ok = prof.inside & (contrast > p.min_contrast)
    if ok.sum() < 8:
        raise _Refused(BorderRefusal.OUTER_EDGE_MISSING)
    ok &= contrast > p.rel_contrast * float(np.median(contrast[ok]))
    d = v - (0.5 * (light + dark))[:, None]
    win = np.abs(kappa) <= search
    w2 = win[:-1] & win[1:]
    fall = (d[:, :-1] >= 0) & (d[:, 1:] < 0) & w2
    rise = (d[:, :-1] < 0) & (d[:, 1:] >= 0) & w2
    n_fall = fall.sum(axis=1)
    # one transition; noise on a shallow ramp may add fall / rise pairs, averaged below
    ok &= (n_fall >= 1) & (rise.sum(axis=1) == n_fall - 1)
    rows = np.arange(len(v))

    def at(j: NDArray[np.intp]) -> F64:
        d0, d1 = d[rows, j], d[rows, j + 1]
        den = np.where(d0 == d1, 1.0, d0 - d1)
        return np.asarray(kappa[j] + d0 / den * (kappa[j + 1] - kappa[j]), np.float64)

    first = np.argmax(fall, axis=1)
    last = fall.shape[1] - 1 - np.argmax(fall[:, ::-1], axis=1)
    return 0.5 * (at(first) + at(last)), ok, slope


class _Outer(NamedTuple):
    normal: F64  # (4, 2) unit normals toward the marker centre
    rho: F64  # (4,)
    scale: F64  # (4,) robust scatter of the inliers (px)
    inliers: NDArray[np.int64]  # (4,) inlier points per side


def _outer_lines(
    xy: F64, ok: NDArray[np.bool_], plan: _Plan, centre: F64, n0: F64, p: BorderParams
) -> _Outer:
    """Robust outer lines (side-major points); refuses missing or locally corrupted sides."""
    n = plan.outer_per_side
    need = max(6, p.min_outer_fraction * n)
    valid = ok.reshape(4, n)
    if np.any(valid.sum(axis=1) < need):
        raise _Refused(BorderRefusal.OUTER_EDGE_MISSING)
    fit = _fit_lines(xy.reshape(4, n, 2), valid, centre, n0)
    count = (fit.weight > 0).sum(axis=1)
    if np.any(count < need):
        raise _Refused(BorderRefusal.OUTER_EDGE_MISSING)
    worst = float(fit.scale.max())
    local = max(p.outer_scale_floor_px, p.max_outer_scale_ratio * float(np.median(fit.scale)))
    if worst > p.max_outer_scale_px or worst > local:
        raise _Refused(BorderRefusal.OUTER_EDGE_NOISY)
    return _Outer(fit.normal, fit.rho, fit.scale, count.astype(np.int64))


class _Observation(NamedTuple):
    points: F64  # (n, 2) observed crossing of each selected site
    ok: NDArray[np.bool_]  # (n,) usable crossing
    slope: F64  # (n,) plateau slope of each site's profile (see _crossings)
    outer: _Outer  # robust outer lines


def _observe(
    g: NDArray[np.float32],
    H: F64,
    plan: _Plan,
    sel: NDArray[np.bool_],
    p: BorderParams,
    width: int,
    height: int,
    search: float,
) -> _Observation:
    """Edge crossings of the selected sites and the robust outer lines through them."""
    centre = _apply(H, np.full((1, 2), plan.cells / 2.0))[0]
    prof = _profiles(g, H, plan, sel, p, width, height)
    crossing, ok, slope = _crossings(prof, p, search)
    xy = prof.base + (crossing * prof.cell)[:, None] * prof.normal
    outer = plan.outer[sel]
    n0, _ = _image_lines(H, plan.line_ends[:4], plan.line_normal[:4])
    return _Observation(xy, ok, slope, _outer_lines(xy[outer], ok[outer], plan, centre, n0, p))


def _geometry_pass(
    g: NDArray[np.float32], seed: F64, plan: _Plan, p: BorderParams, width: int, height: int
) -> F64:
    """Observed outer quad (delta = 0) from the outer edges alone, searched around the seed."""
    H = _homography(plan, seed)
    obs = _observe(g, H, plan, plan.outer, p, width, height, p.search_seed)
    return _intersect(obs.outer.normal, obs.outer.rho)


def _full_pass(
    g: NDArray[np.float32], quad: F64, plan: _Plan, p: BorderParams, width: int, height: int
) -> BorderRefinement:
    everything = np.ones(len(plan.point), bool)
    obs = _observe(g, _homography(plan, quad), plan, everything, p, width, height, p.search)
    # The domain check, once the geometry pass has centred the profiles on the edges (a
    # seed several pixels off would tilt every plateau by itself): over the four sides,
    # then side by side (outer sites come first in the plan, side-major).
    flat = obs.slope[plan.outer & obs.ok]
    if flat.size and _median(flat) > p.max_plateau_slope:
        raise _Refused(BorderRefusal.PLATEAU_NOT_FLAT)
    n_side = plan.outer_per_side
    for side in range(4):
        part = slice(side * n_side, (side + 1) * n_side)
        on_side = obs.slope[part][obs.ok[part]]
        if on_side.size and _median(on_side) > p.max_side_plateau_slope:
            raise _Refused(BorderRefusal.PLATEAU_NOT_FLAT)
    n_out, rho_out = obs.outer.normal, obs.outer.rho
    inner = obs.ok & ~plan.outer
    pts, line, dark = obs.points[inner], plan.line[inner], plan.dark[inner]
    grp, dwt = plan.group[inner], plan.dweight[inner]
    model = _EdgeModel(plan, n_out, rho_out, pts, line, dark, dwt)
    r0 = model.residuals(np.zeros(4)) if len(pts) else np.zeros(0)
    # robust weights per edge group about the group's own median (= its delta, to first
    # order); a group noisier than max_inner_scale_px is not used
    w = np.zeros(len(pts))
    inner_scale = np.full(4, np.nan)
    for gi in range(6):
        m = grp == gi
        if m.sum() >= p.min_inner_points:
            rs = r0[m]
            sc = _mad_scale(rs, 0.05)
            if gi < 4:
                inner_scale[gi] = sc
            if sc <= p.max_inner_scale_px:
                w[m] = _tukey((rs - _median(rs)) / (_TUKEY_C * sc))
    support = ((w > 0)[:, None] * dwt).sum(axis=0)
    observable = support >= p.min_inner_points
    if not observable.any():
        noisy = bool(np.isfinite(inner_scale).any())
        reason = BorderRefusal.INNER_EDGE_NOISY if noisy else BorderRefusal.INNER_EDGE_MISSING
        raise _Refused(reason)
    keep = w > 0
    model = _EdgeModel(plan, n_out, rho_out, pts[keep], line[keep], dark[keep], dwt[keep])
    delta = _solve_delta(model, w[keep], _delta_basis(observable))
    if not np.isfinite(delta).all():
        raise _Refused(BorderRefusal.NUMERICAL_FAILURE)
    cell_mean = float(np.linalg.norm(quad - quad[_NEXT], axis=1).mean() / plan.cells)
    too_big = np.abs(delta).max() > min(p.max_delta_px, p.max_delta_cells * cell_mean)
    if too_big or np.ptp(delta) > p.max_delta_spread_px:
        raise _Refused(BorderRefusal.DELTA_IMPLAUSIBLE)
    corners = model.corners(delta)
    if not np.isfinite(corners).all():
        raise _Refused(BorderRefusal.NUMERICAL_FAILURE)
    return BorderRefinement(
        corners=corners.astype(np.float32),
        delta=delta,
        outer_scale=obs.outer.scale,
        inner_scale=inner_scale,
        n_outer=obs.outer.inliers,
        n_inner=np.round(support).astype(np.int64),
        cell_px=cell_mean,
    )


def _sparse_border(bits: NDArray[np.bool_]) -> bool:
    """True when a pair of opposite sides has fewer than 2 white border-adjacent cells."""
    white = [int(_adjacent_bits(bits, s).sum()) for s in range(4)]
    return min(white[0] + white[2], white[1] + white[3]) < 2


def _convex(q: F64) -> bool:
    e = q[_NEXT] - q
    cross = e[:, 0] * e[_NEXT][:, 1] - e[:, 1] * e[_NEXT][:, 0]
    return bool(np.all(cross > 0) or np.all(cross < 0))


def refine_marker_corners(
    gray: NDArray[np.uint8] | NDArray[np.float32],
    corners: NDArray[np.floating],
    bits: NDArray[np.bool_],
    params: BorderParams | None = None,
) -> BorderRefinement | BorderRefusal:
    """Unbiased outer corners of one ArUco marker, or the reason the view is unreliable.

    ``gray``: 2-D image the marker was detected in, in 0-255 levels (uint8, or float32 on
    the same scale: the contrast thresholds are in gray levels; only a crop around the
    marker is read). ``corners``: (4, 2) seed corners, TL, TR, BR, BL (``detectMarkers``
    output, CORNER_REFINE_CONTOUR or none). ``bits``: the marker's data bits
    (:func:`marker_bits`). ~2 ms for a 130 px marker (numpy + two cv2.remap calls).
    """
    p = params or BorderParams()
    if gray.ndim != 2:
        raise ValueError("gray must be a 2-D image")
    seed = np.asarray(corners, np.float64).reshape(4, 2)
    bits = np.ascontiguousarray(bits, dtype=np.bool_)
    if bits.ndim != 2 or bits.shape[0] != bits.shape[1] or bits.shape[0] < 2:
        raise ValueError("bits must be a square n x n boolean array")
    n_bits = int(bits.shape[0])
    try:
        if not np.isfinite(seed).all():
            raise _Refused(BorderRefusal.DEGENERATE_QUAD)
        sides = np.linalg.norm(seed - seed[_NEXT], axis=1)
        if not _convex(seed):
            raise _Refused(BorderRefusal.DEGENERATE_QUAD)
        if float(sides.min()) < (n_bits + 2) * p.min_cell_px:
            raise _Refused(BorderRefusal.MARKER_TOO_SMALL)
        # work on a float32 crop: every profile stays within ~2 cells of the seed's edges
        margin = 2.1 * float(sides.max()) / (n_bits + 2) + 4.0
        height, width = gray.shape
        x0 = max(0, int(seed[:, 0].min() - margin))
        x1 = min(width, int(seed[:, 0].max() + margin) + 2)
        y0 = max(0, int(seed[:, 1].min() - margin))
        y1 = min(height, int(seed[:, 1].max() + margin) + 2)
        g = np.ascontiguousarray(gray[y0:y1, x0:x1], dtype=np.float32)
        offset = np.array([x0, y0], np.float64)
        use_bits = _sparse_border(bits) if p.use_bit_edges is None else p.use_bit_edges
        plan = _plan(
            bits.tobytes(),
            n_bits,
            p.samples_per_cell,
            p.outer_margin,
            p.inner_end_margin,
            use_bits,
        )
        quad = _geometry_pass(g, seed - offset, plan, p, x1 - x0, y1 - y0)
        result = _full_pass(g, quad, plan, p, x1 - x0, y1 - y0)
        return replace(result, corners=(result.corners + offset).astype(np.float32))
    except _Refused as exc:
        return exc.reason
