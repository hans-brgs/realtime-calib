"""Supported ArUco dictionaries (OpenCV predefined set).

We expose the predefined dictionaries by their OpenCV constant name (e.g.
``DICT_5X5_100``). ``dictionary_capacity`` returns how many distinct markers a
dictionary holds — used to check a ChArUco board fits its dictionary.
"""

from __future__ import annotations

import cv2

# Curated set (one per useful bit size): the meaningful choice is the marker bit
# grid (4x4 reads from farther / lower resolution, 6x6 is more robust but needs
# more pixels). Capacity is fixed at a sensible value per size. Same-dimension
# predefined dictionaries are nested, so extra capacities add no distinct markers.
SUPPORTED_DICTIONARIES: tuple[str, ...] = (
    "DICT_4X4_100",
    "DICT_5X5_100",
    "DICT_6X6_250",
)

# Special-case capacities that are not encoded in the constant name.
_SPECIAL_CAPACITY: dict[str, int] = {"DICT_ARUCO_ORIGINAL": 1024}


def is_supported(name: str) -> bool:
    return name in SUPPORTED_DICTIONARIES


def resolve(name: str) -> cv2.aruco.Dictionary:
    """Return the OpenCV predefined dictionary for a supported name."""
    if not is_supported(name):
        raise ValueError(f"unsupported dictionary: {name!r}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def dictionary_capacity(name: str) -> int:
    """Number of distinct markers in the dictionary (from its name or a special case)."""
    if name in _SPECIAL_CAPACITY:
        return _SPECIAL_CAPACITY[name]
    # Names look like DICT_<n>X<n>_<capacity>.
    return int(name.rsplit("_", 1)[1])
