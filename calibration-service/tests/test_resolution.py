"""Native <-> output intrinsics follow cv2.resize's pixel-centre mapping (ADR-0051)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from calibration_service.resolution import (
    from_legacy_output,
    output_size,
    to_native,
    to_output,
)

K_NATIVE = [[1000.0, 0.0, 960.3], [0.0, 1010.0, 540.7], [0.0, 0.0, 1.0]]


def _blob_centroid(
    native_size: tuple[int, int], point: np.ndarray, out: tuple[int, int]
) -> np.ndarray:
    """Render a Gaussian blob at a native sub-pixel point, resize it, return its centroid."""
    width, height = native_size
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.exp(-((xx - point[0]) ** 2 + (yy - point[1]) ** 2) / (2 * 6.0**2))
    small = cv2.resize(image, out, interpolation=cv2.INTER_AREA)
    ys, xs = np.mgrid[0 : out[1], 0 : out[0]]
    mass = small > 1e-4
    weights = small[mass]
    total = float(weights.sum())
    return np.array([(weights * xs[mass]).sum() / total, (weights * ys[mass]).sum() / total])


@pytest.mark.parametrize(
    ("native_size", "factor"),
    [
        ((1920, 1080), 0.5),
        ((1920, 1080), 1 / 3),
        ((640, 480), 1 / 3),
        ((1280, 720), 0.75),
        ((1280, 720), 1 / 3),
    ],
)
def test_the_output_matrix_projects_where_cv2_resize_puts_the_point(
    native_size: tuple[int, int], factor: float
) -> None:
    # A point projected with the native K, then the native image resized like a
    # consumer does: the output K must project it exactly there. 640x480 at 1/3
    # rounds to 213 px wide, so the effective factor differs from s; 1280x720 at
    # 1/3 is 427 px wide, where rounding and truncation part.
    k_native = np.array(K_NATIVE)
    k_native[0, 2], k_native[1, 2] = native_size[0] / 2 + 0.3, native_size[1] / 2 + 0.7
    out = output_size(native_size, factor)
    k_out = np.array(to_output(k_native.tolist(), native_size, factor))
    for ray in ([0.05, -0.04, 1.0], [-0.1, 0.07, 1.0]):
        native_point = (k_native @ ray)[:2]
        measured = _blob_centroid(native_size, native_point, out)
        assert np.allclose((k_out @ ray)[:2], measured, atol=0.002)
        legacy = (np.diag([factor, factor, 1.0]) @ k_native @ ray)[:2]
        assert np.abs(legacy - measured).max() > 0.1  # what K*s got wrong


def test_to_native_inverts_to_output() -> None:
    for size, factor in (((1920, 1080), 0.5), ((640, 480), 1 / 3), ((1280, 720), 0.25)):
        back = to_native(to_output(K_NATIVE, size, factor), size, factor)
        assert np.allclose(back, K_NATIVE, atol=1e-12)


def test_the_principal_point_moves_by_half_a_pixel_times_one_minus_s() -> None:
    out = to_output(K_NATIVE, (1920, 1080), 0.5)
    assert out[0][0] == pytest.approx(500.0) and out[1][1] == pytest.approx(505.0)
    assert out[0][2] == pytest.approx(0.5 * 960.3 - 0.25)
    assert out[1][2] == pytest.approx(0.5 * 540.7 - 0.25)
    assert out[2] == [0.0, 0.0, 1.0]


def test_native_resolution_is_the_identity() -> None:
    assert to_output(K_NATIVE, (1920, 1080), 1.0) == K_NATIVE
    assert np.array_equal(to_native(K_NATIVE, (1920, 1080), 1.0), K_NATIVE)


def test_a_legacy_matrix_converts_exactly() -> None:
    # Schema-1 sessions stored s * K_native: dividing back is exact, so the
    # converted matrix is what the current writer produces from the native one.
    legacy = (np.diag([0.5, 0.5, 1.0]) @ np.array(K_NATIVE)).tolist()
    assert np.allclose(
        from_legacy_output(legacy, (1920, 1080), 0.5), to_output(K_NATIVE, (1920, 1080), 0.5)
    )
