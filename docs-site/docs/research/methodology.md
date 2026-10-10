---
sidebar_position: 1
description: "What realtime-calib computes, grounded in sources: ChArUco intrinsics, diversity-based keyframe selection, timestamp synchronization, pairwise stereo poses, anchor chaining and a rigidity-constrained bundle adjustment."
keywords: [camera calibration methodology, bundle adjustment, stereo calibration, ChArUco detection, OpenCV calibration]
---

# Methodology

This section describes **what** realtime-calib computes and points to the authoritative sources for the underlying theory, rather than re-deriving it. If you want the mathematics, follow the references: they are the ground truth. Where Caliscope is cited, the version is given, since its pipeline has changed across releases.

## Capture and replay

Live detection only drives the operator's feedback. Every solve re-reads the **native recordings** of the session folder and re-detects the board on them, with the settings chosen in the Prepare step. Recordings are MJPG in Matroska, written at a configurable JPEG quality (default 95).

## Corner detection

- **ChArUco**: OpenCV's `CharucoDetector` (OpenCV ≥ 4.8), followed by `cornerSubPix` on the interpolated chessboard corners. That pass is load-bearing: on OpenCV 4.13 it removes a +0.48 px offset of `detectBoard`'s corners ([opencv#25539](https://github.com/opencv/opencv/issues/25539)); a view whose refinement fails is dropped rather than kept with raw corners.
- **Single ArUco marker**: OpenCV's contour refinement places a marker's corners 1 to 2 px inward on real footage (blur and gamma push the apparent edge toward its dark side), which made every single-marker world 1.3 to 3 % too large. At compute time, the corners are instead refined from the marker's own **black border**: being exactly one cell wide, its inner edges move outward by the same amount its outer edges move inward, and the homography of the corrected quad predicts the true edges under any perspective. A view whose border cannot be read reliably is dropped, never kept with the biased corners, and counted per camera. The live overlay keeps the contour refinement.
- **Inverted boards** are re-inverted before detection, as Caliscope does (v0.5.4 and v0.11.5).
- The ArUco detector thresholds each image with two adaptive windows (3 and 23 px) instead of OpenCV's default three, as Caliscope v0.11.5 sets on its ChArUco tracker: about 35 to 44 % less detection time, with solves unchanged within their own scatter.

## Intrinsic calibration

Per-camera intrinsics and distortion are estimated from ChArUco detections using OpenCV:

- `cv2.calibrateCameraExtended` (the ChArUco-specific `calibrateCameraCharucoExtended` was removed in OpenCV ≥ 4.7) with `CALIB_USE_INTRINSIC_GUESS` and no distortion-model flag: the classic **5-coefficient** distortion model `[k1, k2, p1, p2, k3]`, with a free aspect ratio. Caliscope v0.5.4 calls `cv2.calibrateCamera` without any flag; v0.11.5 adds the intrinsic guess.
- The guess is Caliscope v0.11.5's seed: focal length `max(width, height)`, principal point at `((W − 1)/2, (H − 1)/2)`. Unlike OpenCV's own initialization, it does not depend on the order of the views: on the same 50 keyframes of a real camera, OpenCV's initialization landed anywhere between 1.4 and 6.5 px of RMS error depending on their order. OpenCV's initialization only stands in when the seeded solve fails.
- The solve runs at native resolution. The camera matrix is then mapped to the output resolution at pixel centres, `c' = s · (c + 0.5) − 0.5`, which is how `cv2.resize` maps pixels; a plain `s · K` would leave the principal point `(1 − s)/2` px off.

**Sources**

- OpenCV camera calibration documentation: [`calibrateCamera`](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html) and the ArUco/ChArUco modules.
- Caliscope's intrinsic pipeline: [Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause).
- The choice of distortion model is discussed with its literature in [Calibration best practices](/docs/reference/calibration-best-practices#distortion-model).

### Keyframe selection

Before solving, realtime-calib picks a diverse, capped subset of the detections:

1. **Stride**: decode and detect one frame every N within the trimmed clip.
2. **Structural gates**: the board found, at least 6 corners, corners not near-collinear (the solver's own hard floors).
3. **Diversity anchors**: farthest-point sampling over a 3-D feature (board **tilt**, and the detection **centroid** as image fractions) places as many anchors as the cap (default 50).
4. **Sharpest per anchor**: every candidate is assigned to its nearest anchor, and the sharpest candidate of each cell is kept.

Diversity decides how many keyframes and where; sharpness (the Laplacian variance over the board region, compared only within a cell) decides which frame represents each spot. There is no absolute blur gate, so a uniformly dim sweep still calibrates. The cap of 50 rests on a measurement: on four identical cameras, their focal estimates scattered by about 10 px at 25 keyframes and settled to 4 to 6 px at 50, where two different selectors also agreed within 0.4 %. Nine alternative selectors were evaluated on 12 real cameras against held-out views and against the downstream extrinsic solve; none beat this one consistently, Caliscope v0.11.5's included.

**Sources**: farthest-point sampling is a standard diversity-sampling technique; the coverage / diversity goal follows Caliscope's approach to keyframe quality.

### Coverage and projection uncertainty

- **Coverage** is a quad accumulation map: each keyframe's board hull is rasterized on a grid, and a cell counts when its centre lies inside the hull, so the metric is not inflated by cells an edge merely touches.
- **Projection uncertainty**: the intrinsic covariance, with the per-view poses marginalized out, is propagated to each cell of the image, with the rotation it implies projected out. Two covariances are computed (independent corner noise, which is OpenCV's own covariance, and a cluster-robust one by view) and the larger reading is kept. Cells past the fold of the radial distortion, where no ray reaches the pixel, are reported as outside the model. On real cameras, the figure matched the disagreement between solves of random halves of the data to within a factor of about 1.5.

## Extrinsic calibration

Every camera's 6-DoF pose is recovered in a **single shared coordinate frame** (the anchor's), from synchronized multi-camera detections of the target:

1. **Timestamps**: each frame carries the camera driver's buffer timestamp (V4L2, `CLOCK_MONOTONIC`), taken at grab. A base is chosen once per camera opening: the kernel stamps when they are plausible, the host clock otherwise. The kernel stamps lead the host's post-decode stamps by 20 to 50 ms, and by a varying amount. The base each camera used is recorded with the sweep.
2. **Grouping**: frames are grouped into synchronized instants with a window derived from the recorded frame intervals, just under one frame period, and a quorum of 2 cameras. The operator can tighten the maximum spread allowed within a group.
3. **Motion gate**: the cameras do not expose together, so a member captured a few milliseconds off its group's instant sees a moving target elsewhere. The compute tracks each member's corners into the neighbouring frames (Lucas–Kanade) for the target's image speed; a group whose worst member is more than 0.5 native px off (speed × time offset) is not used. A quarter of the group budget stays guaranteed, and the compute falls back to every group, with a warning, if the gated ones do not solve.
4. **Group selection**: the sharpest groups are kept up to the operator's cap, spread over the sweep in temporal bins.
5. **Pairwise poses**: for each co-visible camera pair, the relative transform is estimated with `cv2.stereoCalibrate` on their shared views (in normalized coordinates).
6. **Chaining from the anchor**: the pairwise estimates form a co-visibility graph; each camera's pose is chained from the **anchor** (camera index 0, fixed as identity) along the lowest-cumulative-error path (Dijkstra), with bridge-filling so indirect routes can beat noisy direct edges.
7. **Triangulation**: the target's corners are triangulated (DLT over all observing rays) into a 3D point cloud.
8. **Bundle adjustment**: `scipy.optimize.least_squares` (trf, sparse Jacobian) jointly refines every non-anchor pose and the 3D points, in two passes as Caliscope v0.11.5 does: a linear pass, then a robust `soft_l1` pass at a 1 px residual scale, converted with the array's median focal length so the outlier threshold does not drift with the lens. The anchor stays fixed, which removes the gauge freedom.

**Imported videos** without real timestamps are synchronized the way Caliscope aligns bare videos (an inferred common-duration grid, then a greedy pass): on a real 4-camera Caliscope dataset, the slots match Caliscope's own sync map 396 out of 396.

### Target rigidity

Reprojection alone lets the solver bend the reconstructed target to absorb detection and sync noise, and leaves the world scale weakly constrained. The bundle adjustment therefore adds **rigidity constraints**, as Caliscope v0.11.5 does: within each group, the distance between two reconstructed corners must match the printed geometry, with a tolerance σ = 2 mm (so 2 mm of deformation costs about as much as 1 px of reprojection).

- **Single marker**: its 4 sides and 2 diagonals.
- **ChArUco**: the board's truss (neighbours and both diagonals of every cell), keeping every edge whose two corners the view triangulated, plus braces between the view's four extreme detected corners so that a partial view stays rigid across every fold line.

The constraint rows go through the same robust pass. The reported reprojection error stays reprojection-only. **Board rigidity** (the RMS deviation of all within-group corner distances from the printed ones, in mm) is reported next to it as an independent judge: it measures the reconstruction against the physical target, which no solver parameter can game.

### Minimize

Minimize is Caliscope's quality loop: the worst 2.5 % of observations (by reprojection error, over the whole array) are dropped, and the bundle adjustment is run again with the same rigidity constraints.

### Reported errors

All extrinsic errors are reported in pixels at the **output resolution**, like the intrinsic ones. The overall RMSE is re-aggregated from the per-camera terms weighted by their observation counts. When comparing with Caliscope, mind two differences: Caliscope v0.11.5 reports its RMSE after its own 2.5 % filter, and at the resolution of the videos it was given.

**Sources**

- OpenCV `stereoCalibrate`, triangulation and optical-flow documentation: [calib3d module](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html), [`calcOpticalFlowPyrLK`](https://docs.opencv.org/4.x/dc/d6b/group__video__track.html).
- `scipy.optimize.least_squares`: [SciPy docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html).
- Caliscope's extrinsic and bundle-adjustment implementation: [Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause), v0.11.5 for the two-pass robust solve and the rigidity constraints.

## Framing, alignment and checks

- **Framing on a target**: the world is moved onto a target lying on the floor, its printed face up. The face is decided by the corner order (a ChArUco board's chessboard corners run the other way from a marker's), not by where the cameras are.
- **Alignment on a reference** fits the solved rig onto a previous calibration of the same room, by matching camera identities: a yaw and a horizontal shift that keep the framed floor, or a full rigid (Kabsch) fit.
- **Pre-export checks**: epipolar consistency per pair (the median symmetric epipolar distance of the solve's own observations), target rigidity, framing and cameras above the floor. With a site template, the implied scale from tape-measured camera distances is the weighted ratio `Σ(m·d/σ²) / Σ(d²/σ²)`, Caliscope v0.11.5's formula for its distance cues; realtime-calib only reports it, against bands at 2σ and 3σ of its own uncertainty.

:::info Why we cite instead of explain
realtime-calib's contribution is making this pipeline **real-time, single-pass and inspectable**, not the calibration theory itself. We ground every claim on Caliscope and OpenCV rather than restating derivations, and every change to the pipeline is judged on recorded sessions against a judge chosen for the question (ground truth, held-out views, target rigidity), never on the displayed RMSE alone.
:::
