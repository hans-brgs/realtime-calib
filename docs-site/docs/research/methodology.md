---
sidebar_position: 1
description: "What realtime-calib computes, grounded in sources: ChArUco intrinsics, diversity-based keyframe selection, pairwise stereo poses, anchor chaining and bundle adjustment."
keywords: [camera calibration methodology, bundle adjustment, stereo calibration, ChArUco detection, OpenCV calibration]
---

# Methodology

This section describes **what** realtime-calib computes and points to the
authoritative sources for the underlying theory, rather than re-deriving it. If
you want the mathematics, follow the references — they are the ground truth.

## Intrinsic calibration

Per-camera intrinsics and distortion are estimated from ChArUco detections using
OpenCV:

- ChArUco corners are interpolated, then solved with `cv2.calibrateCameraExtended`
  (the ChArUco-specific `calibrateCameraCharucoExtended` was removed in OpenCV ≥ 4.7)
- flag `CALIB_USE_INTRINSIC_GUESS` only — no distortion-model flags, matching
  Caliscope's plain `cv2.calibrateCamera` call: the classic **5-coefficient**
  distortion model `[k1, k2, p1, p2, k3]`, with a free aspect ratio

**Sources**

- OpenCV camera calibration documentation —
  [`calibrateCamera`](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html) and
  the ArUco/ChArUco modules.
- Caliscope's intrinsic pipeline —
  [Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause).

### Keyframe selection

Before solving, realtime-calib picks a small, diverse subset of the captured
detections — this coverage-aware selection is where the pipeline earns its
single-pass, real-time behaviour:

1. **Stride** — decimate high-fps runs (keep one detection every N).
2. **Quality gates** — drop frames that are blurry (Laplacian-variance sharpness
   below a floor), carry too few ChArUco corners, or whose corners are degenerate
   / poorly spread.
3. **Farthest-point sampling** — if more candidates remain than the cap (default
   25), describe each by a 3-D feature — board **tilt** (0–45°, normalised) and the
   detection **centroid** (x, y as image fractions) — then start from the frame
   with the most corners and greedily add the candidate farthest from those
   already selected.

The result maximises **orientation and sensor-region coverage** rather than raw
frame count, which is what keeps the intrinsic solve well-conditioned.

**Sources**: farthest-point sampling is a standard diversity-sampling technique;
the coverage / diversity goal follows Caliscope's approach to keyframe quality.

## Extrinsic calibration

Every camera's 6-DoF pose is recovered in a **single shared coordinate frame**
(the anchor's), from synchronized multi-camera detections of the board:

1. **Pairwise poses** — for each co-visible camera pair, the relative transform is
   estimated with `cv2.stereoCalibrate` on their shared board views (in normalized
   coordinates).
2. **Chaining from the anchor** — the pairwise estimates form a co-visibility
   graph; each camera's pose is chained from the **anchor** (camera index 0, fixed
   as identity) along the lowest-cumulative-error path (Dijkstra), with
   bridge-filling so indirect routes can beat noisy direct edges.
3. **Triangulation** — the board corners are triangulated (DLT over all observing
   rays) into a 3D point cloud.
4. **Bundle adjustment** — `scipy.optimize.least_squares` (trf, sparse Jacobian)
   jointly refines every non-anchor pose and the 3D points, minimizing
   reprojection error. The anchor stays fixed, which removes the gauge freedom.

**Sources**

- OpenCV `stereoCalibrate` and triangulation documentation —
  [calib3d module](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
- `scipy.optimize.least_squares` —
  [SciPy docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html).
- Caliscope's extrinsic + bundle-adjustment implementation —
  [Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause).

:::info Why we cite instead of explain
realtime-calib's contribution is making this pipeline **real-time and
incremental**, not the calibration theory itself. We ground every claim on
Caliscope and OpenCV rather than restating derivations.
:::
