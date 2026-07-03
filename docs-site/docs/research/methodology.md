---
sidebar_position: 1
---

# Methodology

This section describes **what** realtime-calib computes and points to the
authoritative sources for the underlying theory, rather than re-deriving it. If
you want the mathematics, follow the references — they are the ground truth.

## Intrinsic calibration

Per-camera intrinsics and distortion are estimated from ChArUco detections using
OpenCV:

- `cv2.aruco.calibrateCameraCharucoExtended`
- flags `CALIB_USE_INTRINSIC_GUESS + CALIB_RATIONAL_MODEL + CALIB_FIX_ASPECT_RATIO`
- `perViewErrors` are exposed and drive outlier rejection

**Sources**

- OpenCV camera calibration documentation —
  [`calibrateCamera`](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html) and
  the ArUco/ChArUco modules.
- Caliscope's intrinsic pipeline —
  [Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause).

## Extrinsic calibration

Camera poses are recovered in a single frame:

1. Pairwise relative poses via PnP / `stereoCalibrate`.
2. Transitive chaining from an **anchor** camera.
3. Joint refinement with **bundle adjustment** (`scipy.least_squares`).

**Sources**

- OpenCV `solvePnP` / `stereoCalibrate` documentation.
- `scipy.optimize.least_squares` —
  [SciPy docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html).
- Caliscope's extrinsic + bundle-adjustment implementation.

:::info Why we cite instead of explain
realtime-calib's contribution is making this pipeline **real-time and
incremental**, not the calibration theory itself. We ground every claim on
Caliscope and OpenCV rather than restating derivations.
:::
