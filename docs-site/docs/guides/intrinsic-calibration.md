---
sidebar_position: 3
---

# Intrinsic calibration

Estimate each camera's focal length and distortion, one camera at a time.

:::note Work in progress
Scaffold page — to be expanded with live-overlay screenshots and stopping criteria.
:::

## The idea

Move the board through the camera's field of view. As frames accumulate, the
service selects diverse **keyframes** and estimates the intrinsics live, showing
coverage and per-view reprojection error until the result is stable.

## Under the hood

realtime-calib mirrors Caliscope's intrinsic pipeline:

- `cv2.aruco.calibrateCameraCharucoExtended`
- flags `CALIB_USE_INTRINSIC_GUESS + CALIB_RATIONAL_MODEL + CALIB_FIX_ASPECT_RATIO`
- `perViewErrors` exposed and used for outlier rejection

→ Explanation & sources: [Methodology](/docs/research/methodology)
