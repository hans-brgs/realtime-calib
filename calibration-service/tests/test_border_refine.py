"""Guards of the single-marker border refinement (ADR-0052).

Synthetic ground truth: the marker pattern rendered exactly (supersampled point lookup through
a known homography), optics blur in linear light, then a camera ISP (gamma 2.2 encoding,
unsharp mask, noise) - the chain that moves every edge toward its dark side and biases
CONTOUR's corners ~1.8 px inward.
"""

from __future__ import annotations

import time
from dataclasses import replace

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.detection import BoardDetector
from calibration_service.detection.border_refine import (
    BorderParams,
    BorderRefinement,
    BorderRefusal,
    marker_bits,
    refine_marker_corners,
)
from calibration_service.models.board import BoardType, CalibrationBoard

DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)
CONTOUR = cv2.aruco.DetectorParameters()
CONTOUR.minMarkerPerimeterRate = 0.01
CONTOUR.polygonalApproxAccuracyRate = 0.05
CONTOUR.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_CONTOUR


def _homography(
    rng: np.random.Generator, side_px: float, tilt_deg: float, cells: int
) -> NDArray[np.float64]:
    """Marker cells (u, v) -> image edge coords, from a pinhole view of a tilted marker."""
    f, size_m = 1350.0, 0.297
    z = f * size_m / side_px
    psi = rng.uniform(0, np.pi)
    axis = np.array([np.cos(psi), np.sin(psi), 0.0]) * np.radians(tilt_deg)
    R = cv2.Rodrigues(np.array([0.0, 0.0, rng.uniform(-np.pi, np.pi)]))[0] @ cv2.Rodrigues(axis)[0]
    t = np.array([*(rng.uniform(-0.3, 0.3, 2) * z), z])
    cell = size_m / cells
    S = np.array([[cell, 0, -cells / 2 * cell], [0, cell, -cells / 2 * cell], [0, 0, 1.0]])
    H = np.diag([f, f, 1.0]) @ np.column_stack([R[:, 0], R[:, 1], t]) @ S
    return np.asarray(H / H[2, 2], np.float64)


def render(
    rng: np.random.Generator,
    *,
    bits: NDArray[np.bool_],
    side_px: float = 130.0,
    tilt_deg: float = 0.0,
    blur: float = 1.2,
    motion_px: float = 0.0,
    gamma: float = 2.2,
    sharpen: float = 0.6,
    noise: float = 2.0,
    quiet: float = 1.0,
    ss: int = 6,
) -> tuple[NDArray[np.uint8], NDArray[np.float64]]:
    """Image and true corners (TL, TR, BR, BL, pixel-centre coords) of one marker view."""
    n = bits.shape[0] + 2
    H = _homography(rng, side_px, tilt_deg, n)
    ext = np.array(
        [
            [-quiet - 0.6, -quiet - 0.6],
            [n + quiet + 0.6, -quiet - 0.6],
            [n + quiet + 0.6, n + quiet + 0.6],
            [-quiet - 0.6, n + quiet + 0.6],
        ]
    )
    pe = cv2.perspectiveTransform(ext.reshape(-1, 1, 2), H).reshape(-1, 2)
    origin = np.floor(pe.min(axis=0)) - 24
    H = np.array([[1, 0, -origin[0]], [0, 1, -origin[1]], [0, 0, 1.0]]) @ H
    w, h = (np.ceil(pe.max(axis=0) - origin) + 24).astype(int)
    xs, ys = np.meshgrid((np.arange(w * ss) + 0.5) / ss, (np.arange(h * ss) + 0.5) / ss)
    Hi = np.linalg.inv(H)
    den = Hi[2, 0] * xs + Hi[2, 1] * ys + Hi[2, 2]
    u = (Hi[0, 0] * xs + Hi[0, 1] * ys + Hi[0, 2]) / den
    v = (Hi[1, 0] * xs + Hi[1, 1] * ys + Hi[1, 2]) / den
    refl = np.full(u.shape, 0.12)  # dark floor
    refl[(u >= -quiet) & (u < n + quiet) & (v >= -quiet) & (v < n + quiet)] = 1.0  # quiet zone
    refl[(u >= 0) & (u < n) & (v >= 0) & (v < n)] = 0.03  # black border
    inner = (u >= 1) & (u < n - 1) & (v >= 1) & (v < n - 1)
    iu = np.clip(np.floor(u[inner] - 1).astype(int), 0, n - 3)
    iv = np.clip(np.floor(v[inner] - 1).astype(int), 0, n - 3)
    refl[inner] = np.where(bits[iv, iu], 1.0, 0.03)
    lin = np.asarray(
        cv2.resize(refl.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA), np.float32
    ) * np.float32(0.36)
    lin = np.asarray(cv2.GaussianBlur(lin, (0, 0), blur), np.float32)
    if motion_px > 0:
        k = np.zeros((31, 31), np.float32)
        a = rng.uniform(0, np.pi)
        for s in np.linspace(-motion_px / 2, motion_px / 2, 64):
            k[round(15 + s * np.sin(a)), round(15 + s * np.cos(a))] += 1
        lin = np.asarray(cv2.filter2D(lin, -1, k / k.sum()), np.float32)
    enc = np.clip(lin, 0, None) ** (1 / gamma) * 255.0
    enc = (
        enc + sharpen * (enc - cv2.GaussianBlur(enc, (0, 0), 1.2)) + rng.normal(0, noise, enc.shape)
    )
    image = np.clip(np.round(enc), 0, 255).astype(np.uint8)
    corners = np.array([[0, 0], [n, 0], [n, n], [0, n]], np.float64)
    truth = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), H).reshape(-1, 2) - 0.5
    return image, np.asarray(truth, np.float64)


def seed_corners(
    image: NDArray[np.uint8], marker_id: int, dictionary: cv2.aruco.Dictionary = DICT
) -> NDArray[np.float64]:
    corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, CONTOUR).detectMarkers(image)
    assert ids is not None and marker_id in ids.ravel().tolist()
    return (
        corners[int(np.flatnonzero(ids.ravel() == marker_id)[0])].reshape(4, 2).astype(np.float64)
    )


def inward(est: NDArray[np.floating], truth: NDArray[np.float64]) -> NDArray[np.float64]:
    u = truth.mean(axis=0) - truth
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    return np.asarray(((np.asarray(est, np.float64) - truth) * u).sum(axis=1))


def refined(
    image: NDArray[np.uint8],
    seed: NDArray[np.float64],
    bits: NDArray[np.bool_],
    params: BorderParams | None = None,
) -> BorderRefinement:
    out = refine_marker_corners(image, seed, bits, params)
    assert isinstance(out, BorderRefinement), f"refused: {out}"
    return out


BITS8 = marker_bits(DICT, 8)


def test_marker_bits_of_id_8() -> None:
    expected = np.array([[1, 1, 1, 1], [1, 1, 1, 0], [1, 1, 0, 1], [1, 0, 1, 0]], bool)
    assert np.array_equal(BITS8, expected)


def test_contour_is_biased_inward_and_refinement_removes_it() -> None:
    # The premise and the fix: CONTOUR ~ +1.8 px inward on the ISP chain, refined ~ 0.
    rng = np.random.default_rng(0)
    contour_bias, refined_bias, errors = [], [], []
    for _ in range(12):
        image, truth = render(rng, bits=BITS8, side_px=float(rng.uniform(90, 180)))
        seed = seed_corners(image, 8)
        r = refined(image, seed, BITS8)
        contour_bias.append(inward(seed, truth).mean())
        refined_bias.append(inward(r.corners, truth).mean())
        errors.append(np.linalg.norm(r.corners - truth, axis=1).max())
    assert np.mean(contour_bias) > 1.2
    assert abs(np.mean(refined_bias)) < 0.02
    assert max(errors) < 0.12


@pytest.mark.parametrize(("tilt", "rms_max"), [(20.0, 0.05), (40.0, 0.06), (55.0, 0.15)])
def test_unbiased_under_perspective(tilt: float, rms_max: float) -> None:
    # The fronto-parallel border model (v1) erred ~1 px per side at 40 deg; v2 predicts the
    # inner border edge through the homography.
    rng = np.random.default_rng(int(tilt))
    err, bias, contour = [], [], []
    for _ in range(12):
        image, truth = render(rng, bits=BITS8, tilt_deg=tilt)
        seed = seed_corners(image, 8)
        r = refined(image, seed, BITS8)
        err.append(np.linalg.norm(r.corners - truth, axis=1))
        bias.append(inward(r.corners, truth))
        contour.append(inward(seed, truth))
    assert np.sqrt(np.mean(np.square(err))) < rms_max
    assert abs(np.mean(bias)) < 0.03
    assert np.mean(contour) > 1.2  # under the same tilt, CONTOUR stays inward


def test_scale_of_the_quad_is_exact() -> None:
    # What the world scale depends on: the refined quad's mean side vs the true one.
    rng = np.random.default_rng(3)
    ratios = []
    for _ in range(10):
        image, truth = render(
            rng, bits=BITS8, side_px=float(rng.uniform(80, 200)), tilt_deg=float(rng.uniform(0, 45))
        )
        c = refined(image, seed_corners(image, 8), BITS8).corners.astype(np.float64)
        side = np.linalg.norm(c - c[[1, 2, 3, 0]], axis=1).mean()
        true_side = np.linalg.norm(truth - truth[[1, 2, 3, 0]], axis=1).mean()
        ratios.append(side / true_side)
    assert abs(np.mean(ratios) - 1.0) < 5e-4


def test_directional_motion_blur() -> None:
    # Motion blur makes delta orientation-dependent (one global delta reads 0.53 px RMS
    # under 8 px of it): one delta per side absorbs it.
    rng = np.random.default_rng(8)
    err = []
    for _ in range(10):
        image, truth = render(rng, bits=BITS8, tilt_deg=30.0, motion_px=8.0)
        r = refined(image, seed_corners(image, 8), BITS8)
        err.append(np.linalg.norm(r.corners - truth, axis=1))
    assert np.sqrt(np.mean(np.square(err))) < 0.1


def test_seed_several_pixels_inside_is_recovered() -> None:
    # CONTOUR can sit ~0.2 cell inside on large sharp markers / MJPEG smears: the geometry
    # pre-pass re-centres the profiles before delta is measured. At 0.3 cell inside, the
    # full pass alone loses the outer edges (outer_edge_missing).
    rng = np.random.default_rng(5)
    image, truth = render(rng, bits=BITS8, side_px=150.0, tilt_deg=20.0)
    seed = seed_corners(image, 8)
    centre = seed.mean(axis=0)
    shrunk = centre + (seed - centre) * (1 - 2 * 0.3 / 6)  # 0.3 cell inside on every side
    r = refined(image, shrunk, BITS8)
    assert np.linalg.norm(r.corners - truth, axis=1).max() < 0.1


def _occlude(
    image: NDArray[np.uint8], truth: NDArray[np.float64], side: int, start: float, frac: float
) -> NDArray[np.uint8]:
    a, b = truth[side], truth[(side + 1) % 4]
    out = (a + b) / 2 - truth.mean(axis=0)
    out /= np.linalg.norm(out)
    cell = np.linalg.norm(b - a) / 6
    p0, p1 = a + start * (b - a), a + (start + frac) * (b - a)
    poly = np.array(
        [p0 - 0.3 * cell * out, p1 - 0.3 * cell * out, p1 + 2.5 * cell * out, p0 + 2.5 * cell * out]
    )
    img = image.copy()
    cv2.fillPoly(img, [np.round(poly).astype(np.int32)], 70)
    return np.asarray(cv2.GaussianBlur(img, (0, 0), 1.0), np.uint8)


def _bite(
    image: NDArray[np.uint8],
    truth: NDArray[np.float64],
    side: int,
    start: float,
    frac: float,
    depth: float,
) -> NDArray[np.uint8]:
    """White over the outer ``depth`` cell of the black border along part of a side.

    The outer edge there looks ``depth`` cell inward, with clean single-transition
    profiles: only the robust line fit can tell those points from the edge.
    """
    a, b = truth[side], truth[(side + 1) % 4]
    inward_dir = truth.mean(axis=0) - (a + b) / 2
    inward_dir /= np.linalg.norm(inward_dir)
    cell = np.linalg.norm(b - a) / 6
    p0, p1 = a + start * (b - a), a + (start + frac) * (b - a)
    poly = np.array(
        [
            p0 - 0.6 * cell * inward_dir,
            p1 - 0.6 * cell * inward_dir,
            p1 + depth * cell * inward_dir,
            p0 + depth * cell * inward_dir,
        ]
    )
    img = image.copy()
    cv2.fillPoly(img, [np.round(poly).astype(np.int32)], int(np.percentile(image, 97)))
    return np.asarray(cv2.GaussianBlur(img, (0, 0), 1.0), np.uint8)


def test_a_bite_out_of_one_border_is_rejected_by_the_robust_fit() -> None:
    # 20 % of one side looks 0.2 cell inward: the Tukey fit drops those points, corners
    # stay exact (plain least squares: 0.7-1.3 px off).
    rng = np.random.default_rng(11)
    image, truth = render(rng, bits=BITS8, side_px=140.0, tilt_deg=15.0)
    bitten = _bite(image, truth, side=1, start=0.35, frac=0.2, depth=0.2)
    r = refine_marker_corners(bitten, seed_corners(image, 8), BITS8)
    assert isinstance(r, BorderRefinement)
    assert np.linalg.norm(r.corners - truth, axis=1).max() < 0.1


def _staircase(
    image: NDArray[np.uint8], truth: NDArray[np.float64], side: int, depth_px: float
) -> NDArray[np.uint8]:
    """One side's outer edge in steps of ``depth_px`` every 8 px, like MJPEG macroblocks
    (+-2.5 px steps measured on a real camera's edges)."""
    a, b = truth[side], truth[(side + 1) % 4]
    inward_dir = truth.mean(axis=0) - (a + b) / 2
    inward_dir /= np.linalg.norm(inward_dir)
    length = float(np.linalg.norm(b - a))
    img = image.copy()
    level = int(np.percentile(image, 97))
    for start in np.arange(0.0, length, 16.0):
        p0 = a + (b - a) * start / length
        p1 = a + (b - a) * min(start + 8.0, length) / length
        poly = np.array(
            [
                p0 - 4 * inward_dir,
                p1 - 4 * inward_dir,
                p1 + depth_px * inward_dir,
                p0 + depth_px * inward_dir,
            ]
        )
        cv2.fillPoly(img, [np.round(poly).astype(np.int32)], level)
    return img


def test_a_jagged_side_is_refused_as_noisy() -> None:
    # The first real refusal reason (34 of 51 on the Test sweep): one side's edge points
    # scatter far more than the others' (MJPEG smears).
    rng = np.random.default_rng(18)
    image, truth = render(rng, bits=BITS8, side_px=140.0, tilt_deg=10.0)
    jagged = _staircase(image, truth, side=1, depth_px=5.0)
    assert refine_marker_corners(jagged, seed_corners(image, 8), BITS8) == (
        BorderRefusal.OUTER_EDGE_NOISY
    )


def test_an_implausible_delta_is_refused() -> None:
    # Any real delta exceeds a 0.01 px bound: the guard, not the scene, is under test.
    rng = np.random.default_rng(19)
    image, _ = render(rng, bits=BITS8, side_px=140.0)
    tight = BorderParams(max_delta_px=0.01)
    assert refine_marker_corners(image, seed_corners(image, 8), BITS8, tight) == (
        BorderRefusal.DELTA_IMPLAUSIBLE
    )


@pytest.mark.parametrize(
    ("label", "view"),
    [
        ("cells at 4 blur sigmas", {"side_px": 60.0, "blur": 2.5}),
        ("a 0.5 cell white margin", {"side_px": 140.0, "quiet": 0.5}),
    ],
)
def test_out_of_domain_views_are_refused(label: str, view: dict[str, float]) -> None:
    # Outside the validated domain the corners came out up to ~1.8 px off with no other
    # guard firing: the blur or the background reaches the plateau windows.
    rng = np.random.default_rng(20)
    image, _ = render(rng, bits=BITS8, **view)  # type: ignore[arg-type]
    assert refine_marker_corners(image, seed_corners(image, 8), BITS8) == (
        BorderRefusal.PLATEAU_NOT_FLAT
    ), label


def _narrow_margin(
    image: NDArray[np.uint8], truth: NDArray[np.float64], side: int, keep: float
) -> NDArray[np.uint8]:
    """The background brought to ``keep`` cell of the outer edge along one side only."""
    a, b = truth[side], truth[(side + 1) % 4]
    outward = (a + b) / 2 - truth.mean(axis=0)
    outward /= np.linalg.norm(outward)
    cell = np.linalg.norm(b - a) / 6
    poly = np.array(
        [
            a + keep * cell * outward,
            b + keep * cell * outward,
            b + 3 * cell * outward,
            a + 3 * cell * outward,
        ]
    )
    img = image.copy()
    cv2.fillPoly(img, [np.round(poly).astype(np.int32)], int(np.median(image[:4, :4])))
    return np.asarray(cv2.GaussianBlur(img, (0, 0), 1.0), np.uint8)


def test_a_margin_narrowed_along_one_side_is_refused() -> None:
    # The four-side median would miss it: the worst side is checked on its own.
    rng = np.random.default_rng(22)
    image, truth = render(rng, bits=BITS8, side_px=140.0, tilt_deg=10.0)
    narrowed = _narrow_margin(image, truth, side=1, keep=0.5)
    assert refine_marker_corners(narrowed, seed_corners(image, 8), BITS8) == (
        BorderRefusal.PLATEAU_NOT_FLAT
    )
    wide = _narrow_margin(image, truth, side=1, keep=0.9)
    r = refined(wide, seed_corners(image, 8), BITS8)
    assert np.linalg.norm(r.corners - truth, axis=1).max() < 0.1
    # 0.55 cell on one side: a looser bound (0.6) would let it through 0.09 px off.
    rng = np.random.default_rng(23)
    image, truth = render(rng, bits=BITS8, side_px=140.0, tilt_deg=10.0)
    narrowed = _narrow_margin(image, truth, side=3, keep=0.55)
    assert refine_marker_corners(narrowed, seed_corners(image, 8), BITS8) == (
        BorderRefusal.PLATEAU_NOT_FLAT
    )


def test_deltas_spread_over_the_sides_are_refused() -> None:
    # Per-side deltas never agree to 1e-3 px: the spread branch, not the scene, is tested.
    rng = np.random.default_rng(23)
    image, _ = render(rng, bits=BITS8, side_px=140.0, tilt_deg=20.0)
    tight = BorderParams(max_delta_spread_px=0.001)
    assert refine_marker_corners(image, seed_corners(image, 8), BITS8, tight) == (
        BorderRefusal.DELTA_IMPLAUSIBLE
    )


def test_the_printable_rendering_margin_is_in_the_domain() -> None:
    # render_board_png leaves a 0.9 cell white margin; the physical target has 1.1.
    rng = np.random.default_rng(21)
    err = []
    for _ in range(6):
        image, truth = render(rng, bits=BITS8, side_px=140.0, tilt_deg=20.0, quiet=0.9)
        err.append(
            np.linalg.norm(refined(image, seed_corners(image, 8), BITS8).corners - truth, axis=1)
        )
    assert np.max(err) < 0.1


def test_a_non_finite_seed_is_refused() -> None:
    image = np.full((200, 200), 128, np.uint8)
    seed = np.array([[50.0, 50.0], [150.0, 50.0], [150.0, np.nan], [50.0, 150.0]])
    assert refine_marker_corners(image, seed, BITS8) == BorderRefusal.DEGENERATE_QUAD


def test_heavy_occlusion_is_refused() -> None:
    rng = np.random.default_rng(12)
    image, truth = render(rng, bits=BITS8, side_px=140.0, tilt_deg=15.0)
    occluded = _occlude(image, truth, side=2, start=0.1, frac=0.7)
    r = refine_marker_corners(occluded, seed_corners(image, 8), BITS8)
    assert r in (BorderRefusal.OUTER_EDGE_MISSING, BorderRefusal.OUTER_EDGE_NOISY)


def test_tiny_marker_is_refused() -> None:
    rng = np.random.default_rng(13)
    image, _ = render(rng, bits=BITS8, side_px=20.0, blur=0.6)
    seed = np.array([[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0]])
    assert refine_marker_corners(image, seed, BITS8) == BorderRefusal.MARKER_TOO_SMALL


def test_non_convex_seed_is_refused() -> None:
    image = np.full((200, 200), 128, np.uint8)
    seed = np.array([[50.0, 50.0], [150.0, 50.0], [60.0, 60.0], [50.0, 150.0]])
    assert refine_marker_corners(image, seed, BITS8) == BorderRefusal.DEGENERATE_QUAD


def test_white_on_black_input_is_not_refined() -> None:
    # Contract: the caller passes the black-on-white image the detector saw (an inverted
    # target is inverted BEFORE detection). On the raw inverted image every edge has the
    # wrong polarity and the refinement must refuse, never return corners.
    rng = np.random.default_rng(14)
    image, _ = render(rng, bits=BITS8)
    seed = seed_corners(image, 8)
    assert isinstance(refine_marker_corners(255 - image, seed, BITS8), BorderRefusal)


def test_uint8_and_float32_inputs_agree() -> None:
    rng = np.random.default_rng(15)
    image, _ = render(rng, bits=BITS8)
    seed = seed_corners(image, 8)
    a = refined(image, seed, BITS8).corners
    b = refined(image.astype(np.float32), seed, BITS8).corners
    assert np.abs(a - b).max() < 1e-4


def test_sparse_marker_switches_bit_edges_on() -> None:
    # Id 17: only one side has an inner border edge. Under directional motion blur its other
    # pair's delta is unobservable without the data-bit edges (auto) - 0.4 px off otherwise.
    bits17 = marker_bits(DICT, 17)
    rng = np.random.default_rng(17)
    auto, off = [], []
    for _ in range(8):
        image, truth = render(rng, bits=bits17, tilt_deg=float(rng.uniform(0, 40)), motion_px=6.0)
        seed = seed_corners(image, 17)
        auto.append(np.linalg.norm(refined(image, seed, bits17).corners - truth, axis=1))
        without = refined(image, seed, bits17, BorderParams(use_bit_edges=False))
        off.append(np.linalg.norm(without.corners - truth, axis=1))
    assert np.sqrt(np.mean(np.square(auto))) < 0.1
    assert np.sqrt(np.mean(np.square(off))) > 2 * np.sqrt(np.mean(np.square(auto)))


def test_other_dictionary_size() -> None:
    # 5x5 data bits (7 x 7 cells): the plan is derived from the bits' shape.
    d5 = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    bits = marker_bits(d5, 7)
    rng = np.random.default_rng(55)
    err = []
    for _ in range(6):
        image, truth = render(rng, bits=bits, side_px=150.0, tilt_deg=float(rng.uniform(0, 40)))
        r = refined(image, seed_corners(image, 7, d5), bits)
        err.append(np.linalg.norm(r.corners - truth, axis=1))
    assert np.sqrt(np.mean(np.square(err))) < 0.06


def test_runtime_budget() -> None:
    # The compute path refines every sweep frame: keep it well below detectMarkers (~7 ms on
    # a 1920x1080 frame, 14 ms on a loaded machine). Loose bound for CI machines.
    rng = np.random.default_rng(16)
    image, _ = render(rng, bits=BITS8, side_px=130.0)
    frame = np.full((1080, 1920), 60, np.uint8)
    frame[400 : 400 + image.shape[0], 800 : 800 + image.shape[1]] = image
    seed = seed_corners(frame, 8)
    refine_marker_corners(frame, seed, BITS8)
    t0 = time.perf_counter()
    for _ in range(20):
        refine_marker_corners(frame, seed, BITS8)
    assert (time.perf_counter() - t0) / 20 < 0.010


# --- The detector's compute path (ADR-0052) ------------------------------------------

MARKER_8 = CalibrationBoard(
    board_type=BoardType.ARUCO,
    dictionary="DICT_4X4_100",
    columns=5,
    rows=7,
    marker_id=8,
    marker_size_mm=297.0,
)


def test_the_compute_detector_is_unbiased_and_the_live_one_keeps_contour() -> None:
    rng = np.random.default_rng(31)
    image, truth = render(rng, bits=BITS8, side_px=130.0, tilt_deg=25.0)
    live = BoardDetector(MARKER_8).detect(image)
    compute = BoardDetector(MARKER_8, border_refine=True).detect(image)
    assert live.found and compute.found
    assert live.corners is not None and compute.corners is not None
    assert inward(live.corners, truth).mean() > 1.0  # CONTOUR's inward bias, kept live
    assert np.abs(inward(compute.corners, truth)).max() < 0.1


def test_a_refused_view_is_dropped_and_counted_never_kept_with_contour() -> None:
    rng = np.random.default_rng(32)
    image, truth = render(rng, bits=BITS8, side_px=130.0)
    occluded = _occlude(image, truth, side=2, start=0.1, frac=0.7)
    assert BoardDetector(MARKER_8).detect(occluded).found  # CONTOUR still sees it
    detector = BoardDetector(MARKER_8, border_refine=True)
    detection = detector.detect(occluded)
    assert not detection.found
    assert sum(detector.border_refusals.values()) == 1
    assert detector.border_attempts == 1


def test_an_opencv_error_drops_the_view_as_a_numerical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from calibration_service.detection import detector as detector_module

    def _raises(*_args: object, **_kwargs: object) -> BorderRefinement:
        raise cv2.error("(-215:Assertion failed) degenerate")

    monkeypatch.setattr(detector_module, "refine_marker_corners", _raises)
    rng = np.random.default_rng(34)
    image, _ = render(rng, bits=BITS8, side_px=130.0)
    detector = BoardDetector(MARKER_8, border_refine=True)
    assert not detector.detect(image).found
    assert detector.border_refusals == {"numerical_failure": 1}
    assert detector.border_attempts == 1


def test_an_inverted_target_is_refined_after_inversion() -> None:
    rng = np.random.default_rng(33)
    image, truth = render(rng, bits=BITS8, side_px=130.0)
    board = replace(MARKER_8, inverted=True)
    detection = BoardDetector(board, border_refine=True).detect(255 - image)
    assert detection.found and detection.corners is not None
    assert np.abs(inward(detection.corners, truth)).max() < 0.1


def test_the_chessboard_path_ignores_border_refine() -> None:
    # ChArUco corners are chessboard saddle points, unbiased already: the same corners
    # with or without the flag, and the refinement never runs.
    charuco = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=5, rows=7
    )
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    board = cv2.aruco.CharucoBoard((5, 7), 1.0, 0.75, dictionary)
    page = board.generateImage((500, 700), marginSize=40)
    image = np.asarray(cv2.GaussianBlur(page, (0, 0), 1.0), np.uint8)
    plain = BoardDetector(charuco).detect(image)
    detector = BoardDetector(charuco, border_refine=True)
    flagged = detector.detect(image)
    assert plain.found and plain.corners is not None and flagged.corners is not None
    assert plain.count >= 20
    assert np.array_equal(plain.corners, flagged.corners)
    assert detector.border_attempts == 0 and not detector.border_refusals
