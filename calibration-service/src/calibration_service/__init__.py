"""Real-time multi-camera calibration service."""

from __future__ import annotations

import os

# OpenBLAS's runtime CPU auto-detection can select a kernel that emits an illegal
# opcode on some AVX-512 CPUs, hard-crashing (SIGILL) the heavy linear algebra in
# cv2.calibrateCamera (the fault is inside libopenblas, not OpenCV). Pin a widely
# safe AVX2 kernel before numpy/OpenBLAS loads. Set here because the package init
# runs before any submodule imports cv2/numpy, so the whole service is covered.
os.environ.setdefault("OPENBLAS_CORETYPE", "Haswell")

__version__ = "0.0.0"
