---
sidebar_position: 4
title: "Intrinsic calibration — focal length & distortion"
sidebar_label: Intrinsic calibration
description: "Estimate each camera's focal length and distortion: capture a sweep with live coverage overlays, tune the replay, and solve on diverse keyframes."
keywords: [camera intrinsic calibration, focal length, lens distortion, reprojection error, ChArUco intrinsics]
---

# Intrinsic calibration

Estimate each camera's focal length and distortion, one camera at a time.

:::note Work in progress
Scaffold page — to be expanded with live-overlay screenshots and stopping criteria.
:::

## The flow

Intrinsics are computed **on demand**, not in a continuous live loop. Each camera
goes through four phases:

1. **Capture** — record a sweep while moving the board through the field of view.
   A live coverage overlay shows which regions and orientations you've hit.
2. **Prepare** — replay the recording and tune what the solver will use: **trim**
   the clip, set a **sampling stride**, and cap the number of frames.
3. **Computing** — the service selects diverse **keyframes** and solves.
4. **Results** — inspect the estimated parameters, the overall reprojection error
   and coverage before accepting.

Keyframe selection maximises **coverage and pose diversity**, not raw frame count:
a cheap stride first, then a greedy pick by board tilt and image position (default
cap: 25 keyframes).

## Under the hood

realtime-calib follows Caliscope's intrinsic pipeline, adapted to current OpenCV:

- ChArUco corners are interpolated, then solved with
  **`cv2.calibrateCameraExtended`** — the ChArUco-specific
  `calibrateCameraCharucoExtended` was removed in OpenCV ≥ 4.7.
- Caliscope flags: `CALIB_USE_INTRINSIC_GUESS + CALIB_RATIONAL_MODEL +
  CALIB_FIX_ASPECT_RATIO` — an 8-coefficient rational distortion model, seeded
  with a guess, with fixed aspect ratio.

→ Explanation & sources: [Methodology](/docs/research/methodology)

→ See also: [Calibration best practices](/docs/reference/calibration-best-practices)
— how to capture a good intrinsic sweep.
