"""Real-time multi-camera calibration service."""

from __future__ import annotations

import os

# OpenBLAS's runtime CPU auto-detection can select a kernel that emits an illegal
# opcode on some AVX-512 CPUs, hard-crashing (SIGILL) the heavy linear algebra in
# cv2.calibrateCamera (the fault is inside libopenblas, not OpenCV). Pin a widely
# safe AVX2 kernel before numpy/OpenBLAS loads. Set here because the package init
# runs before any submodule imports cv2/numpy, so the whole service is covered.
os.environ.setdefault("OPENBLAS_CORETYPE", "Haswell")

# A camera that stops streaming parks its grab in V4L2's select() for this many
# seconds (OpenCV's default is 10, cap_v4l.cpp, read at the first capture). On the
# rig, a camera reopened in quick view cycles sometimes never streams again: each
# read then cost 10 s, and the camera's stop, and the reconcile behind it, waited
# that long (ADR-0050, rule 6). Frames arrive every 20 to 133 ms; 2 s still leaves
# a slow camera start ample time.
os.environ.setdefault("OPENCV_VIDEOIO_V4L_SELECT_TIMEOUT", "2")

__version__ = "0.0.0"
