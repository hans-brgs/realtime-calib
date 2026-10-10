---
sidebar_position: 4
title: "Intrinsic calibration — focal length & distortion"
sidebar_label: Intrinsic calibration
description: "Estimate each camera's focal length and distortion: capture a sweep with live coverage overlays, tune the replay, solve on diverse keyframes and read the projection uncertainty."
keywords: [camera intrinsic calibration, focal length, lens distortion, reprojection error, ChArUco intrinsics, projection uncertainty]
---

# Intrinsic calibration

Estimate each camera's focal length, principal point and distortion, one camera at a time, from the ChArUco board.

## The flow

Intrinsics are computed **on demand**, from a recording, not in a continuous live loop. Each camera goes through four phases:

1. **Capture**: record a sweep while moving the board through the field of view. The live view shows the detected board, and the **live gauges** (marked *indicative*) show the board coverage so far (target ≥ 50 %) and whether the current frame is sharp enough.
2. **Prepare**: replay the recording and tune what the solver will use: **trim** the clip (start / end), set the **sampling stride** (1 frame every N, default 5) and the **keyframe cap** (default 50, from 6 to 100).
3. **Computing**: the service re-detects the board on the native recording, selects diverse **keyframes** and solves.
4. **Results**: inspect the estimated parameters and the quality figures below before moving to the next camera.

## Reading the results

- **Reprojection error**: the RMS distance, in pixels at the output resolution, between the detected corners and where the solved model puts them. Useful, but a low value over poor coverage can still hide a bad calibration.
- **Field-of-view coverage**: the share of the image covered by at least one keyframe's board. 70 % or more reads green; the outer ring of the image is hard to reach with a ChArUco board's interior corners, so 100 % is not expected.
- **Board orientations**: how many of 8 tilt directions the keyframes span (4 or more reads green).
- **Projection uncertainty (covered / never covered)**: how far the solved model could misplace a pixel's ray (1 σ, in output pixels), estimated from the solve itself. The first figure is where three or more keyframes covered the image, the second where none did, where the model extrapolates. The **Uncertainty** map shows it cell by cell: re-shoot any red zone you will rely on. Grey cells lie past the lens model's distortion fold, where no ray reaches the pixel, so no figure is given.
- **Keyframes used** and their sharpness.

## Keyframe selection

The solve does not take every frame. After the structural gates (the board found, at least 6 corners, not a near-collinear sliver), the selection runs in two passes:

1. **Where**: farthest-point sampling over board **tilt** and **image position** places as many spots as the keyframe cap allows, spread over the poses you showed.
2. **Which**: every candidate frame goes to its nearest spot, and the **sharpest frame of each spot** is kept.

Diversity decides how many keyframes and where; sharpness decides which frame to take at each spot. There is no absolute blur threshold, so a sweep in dim light still calibrates, and coverage is never traded for the crispest few frames (which cluster on the frontal, central hold). A low cap samples the spread of your sweep coarsely; the default of 50 is where identical cameras agree on their focal length.

## Under the hood

realtime-calib follows Caliscope's intrinsic pipeline, adapted to current OpenCV:

- ChArUco corners are detected with OpenCV's `CharucoDetector`, then refined with `cornerSubPix`.
- The solve is **`cv2.calibrateCameraExtended`** with `CALIB_USE_INTRINSIC_GUESS` and no distortion-model flag: the classic **5-coefficient** model `[k1, k2, p1, p2, k3]`, with a free aspect ratio.
- It starts from the same seed as Caliscope v0.11.5: focal length `max(width, height)`, principal point at the image centre. That seed does not depend on the order of the views, so the same keyframes always give the same result. OpenCV's own initialization only stands in if that solve fails.

## Recomputing after an extrinsic solve

The extrinsic solve depends on every camera's intrinsics. Recomputing a camera's intrinsics once the array is solved therefore discards the extrinsic solve; the web app asks for confirmation first. The recorded sweep is kept, and the Extrinsic step then offers **"Recompute from the recorded sweep"**.

→ Explanation & sources: [Methodology](/docs/research/methodology)

→ See also: [Calibration best practices](/docs/reference/calibration-best-practices): how to capture a good intrinsic sweep.
