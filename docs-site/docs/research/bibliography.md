---
sidebar_position: 3
description: "The tools and literature behind realtime-calib: Caliscope, OpenCV, SciPy, LiveKit and the peer-reviewed references grounding the method."
keywords: [camera calibration references, Caliscope, OpenCV, multi-camera calibration papers]
---

# Bibliography & references

The tools and literature behind realtime-calib.

## Software & tools

- **Caliscope** — the conceptual reference for the calibration logic (board
  definitions, ChArUco calibration, PnP/stereo extrinsics, bundle adjustment).
  BSD-2-Clause. [github.com/mprib/caliscope](https://github.com/mprib/caliscope)
- **OpenCV** — camera calibration, ArUco/ChArUco (`CharucoDetector`, OpenCV ≥ 4.8).
  [docs.opencv.org](https://docs.opencv.org/)
- **SciPy** — `scipy.optimize.least_squares` for bundle adjustment.
  [scipy.org](https://scipy.org/)
- **LiveKit** — real-time transport for camera streams and overlays.
  [livekit.io](https://livekit.io/)

For the peer-reviewed literature behind the method and the capture recommendations,
see [Calibration best practices](/docs/reference/calibration-best-practices).
