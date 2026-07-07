---
sidebar_position: 3
---

# Bibliography & references

The authoritative sources realtime-calib builds on.

## Software & references

- **Caliscope** — the conceptual reference for the calibration logic (board
  definitions, ChArUco calibration, PnP/stereo extrinsics, bundle adjustment).
  BSD-2-Clause. [github.com/mprib/caliscope](https://github.com/mprib/caliscope)
- **OpenCV** — camera calibration, ArUco/ChArUco (`CharucoDetector`, OpenCV ≥ 4.8).
  [docs.opencv.org](https://docs.opencv.org/)
- **SciPy** — `scipy.optimize.least_squares` for bundle adjustment.
  [scipy.org](https://scipy.org/)
- **aniposelib** — export target for downstream reconstruction.
  [github.com/lambdaloop/aniposelib](https://github.com/lambdaloop/aniposelib)
- **LiveKit** — real-time transport for camera streams and overlays.
  [livekit.io](https://livekit.io/)

:::note Work in progress
Foundational papers (camera models, distortion, bundle adjustment) will be added
here with full citations.
:::
