---
sidebar_position: 2
---

# Define a calibration board

Configure the board realtime-calib will detect. **ChArUco** is recommended
(robust detection plus checkerboard-corner accuracy).

:::note Work in progress
Scaffold page — to be expanded with board configuration UI and print guidance.
:::

## Board types

Following Caliscope's model, the supported families are **ChArUco**, **ArUco** and
plain **chessboard**. Board detection uses OpenCV's `CharucoDetector`
(OpenCV ≥ 4.8).

## Geometry and scale

The board's square/marker counts and its **physical scale** must match the board
you actually printed. Scale is set by measurement so that translations come out in
real-world units.

→ Reference: [Data entities](/docs/reference/entities) · `CalibrationBoard`
