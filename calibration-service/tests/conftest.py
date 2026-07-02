"""Pytest setup shared by all tests.

Set OPENBLAS_CORETYPE before any test module imports cv2/numpy (and so the
capability-probe subprocess inherits it): OpenBLAS's runtime CPU detection
otherwise picks a kernel that SIGILLs on some AVX-512 CPUs, killing the whole
pytest process during cv2.calibrateCamera. Mirrors calibration_service/__init__.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_CORETYPE", "Haswell")
