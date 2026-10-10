"""Intrinsics between a camera's native and output resolutions (ADR-0015, ADR-0051).

A camera is calibrated at its native resolution and reported at ``native x s``
(the operator's resize factor). A resampler such as ``cv2.resize`` maps pixel
CENTRES, not pixel indices: ``x_out + 0.5 = s_x (x_in + 0.5)``, where
``s_x = W_out / W_in`` is the effective factor of the ROUNDED output size. The
former ``K_out = s K_native`` dropped both: its principal point was off by
``(1 - s) / 2`` px (0.25 px at s = 1/2), up to 0.5 px once the size rounds.

The mapping is an affine map of the image plane, so it applies to K by a left
product (``K_out = A K_native``) and the distortion coefficients, defined on
normalized coordinates, are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

Size = tuple[int, int]  # (width, height)


def output_size(native_size: Size, factor: float) -> Size:
    """The output image size for ``factor`` — what an export declares as ``size``."""
    width, height = native_size
    return (round(width * factor), round(height * factor))


def _image_map(native_size: Size, factor: float) -> NDArray[np.float64]:
    """The 3x3 affine map from native to output pixel coordinates."""
    out_width, out_height = output_size(native_size, factor)
    sx = out_width / native_size[0]
    sy = out_height / native_size[1]
    return np.array([[sx, 0.0, 0.5 * (sx - 1.0)], [0.0, sy, 0.5 * (sy - 1.0)], [0.0, 0.0, 1.0]])


def to_output(
    matrix: Sequence[Sequence[float]], native_size: Size, factor: float
) -> list[list[float]]:
    """The camera matrix at ``native x factor``, from its native ``matrix``."""
    if factor == 1.0:
        return [[float(v) for v in row] for row in matrix]
    scaled = _image_map(native_size, factor) @ np.asarray(matrix, np.float64)
    return [[float(v) for v in row] for row in scaled]


def to_native(
    matrix: Sequence[Sequence[float]], native_size: Size, factor: float
) -> NDArray[np.float64]:
    """The native camera matrix back from its output ``matrix`` (inverse of to_output)."""
    native = np.asarray(matrix, np.float64).copy()
    if factor == 1.0:
        return native
    return np.asarray(np.linalg.solve(_image_map(native_size, factor), native), np.float64)


def from_legacy_output(
    matrix: Sequence[Sequence[float]], native_size: Size, factor: float
) -> list[list[float]]:
    """An output matrix written as ``s K_native`` (schema 1), in today's convention.

    Exact: dividing the first two rows by ``s`` recovers the native matrix the
    legacy writer scaled, which ``to_output`` then maps correctly.
    """
    native = np.asarray(matrix, np.float64).copy()
    native[:2] /= factor
    return to_output(native.tolist(), native_size, factor)
