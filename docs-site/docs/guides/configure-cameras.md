---
sidebar_position: 3
description: "Detect the USB cameras, set one shared capture configuration (resolution, frame rate, resize factor) and order the rig — the first camera anchors the extrinsics."
keywords: [USB camera calibration, multi-camera rig, camera detection, multi-camera USB calibration software]
---

# Configure cameras

Detect the USB cameras, set one shared capture configuration, and order them —
the first camera is the extrinsic anchor.

:::note Work in progress
Scaffold page — to be expanded with screenshots from the **Camera Setup** step.
:::

## What happens here

The `calibration-service` enumerates connected USB cameras (V4L2) and publishes a
live preview for each over LiveKit. In the web app's **Camera Setup** view you
can:

- **Detect / re-detect** the connected cameras and confirm each is streaming.
- Set the **capture configuration** — resolution, frame rate and resize factor.
  These are **shared by all cameras** and only offer the modes common to every
  detected camera.
- **Order the cameras** by drag-and-drop. The camera at the top (**index 0**) is
  the **anchor** used to chain extrinsic poses.

## Capture configuration

Because the rig is calibrated as one system, resolution and frame rate are picked
**once and applied to every camera** — the selectors only offer modes supported by
all detected cameras. Frame rate follows a **60 / 30 / 15** ladder, capped by each
camera's native maximum for the chosen resolution.

## Resolution vs. resize factor

Two independent controls change how many pixels calibration works on — but they
trade off very differently:

- **Resolution** selects the camera's **native capture mode**. A lower mode costs
  less compute, but beware: **most USB cameras produce a lower resolution by
  cropping the sensor**, not by scaling it down — so dropping the resolution often
  **narrows the field of view**. You gain speed but lose coverage.
- **Resize factor** *(s)* is a **software downscale** (`cv2.resize`) applied to the
  captured frame — 1, 0.75, 0.5, ⅓ or 0.25. It keeps the **full field of view**
  and simply lowers the pixel count, trading fine detail for compute.

So to cut compute **without losing field of view**, prefer lowering the resize
factor over dropping to a smaller native resolution.

Whichever you choose, the resulting calibration resolution is recorded so
intrinsics stay consistent with the images they were computed from: the stored
**K** corresponds to that resolution, and **K_out = s·K** is written on export.

→ Reference: [Calibration output files](/docs/reference/output-calibration-files)
