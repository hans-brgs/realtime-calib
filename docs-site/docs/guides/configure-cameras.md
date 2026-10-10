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

Two independent controls set a pixel count, and they act at different places:

- **Resolution** selects the camera's **native capture mode**: what is recorded and calibrated. A lower mode records and computes faster, but beware: **most USB cameras produce a lower resolution by cropping the sensor**, not by scaling it down, so dropping the resolution often **narrows the field of view**. You gain speed but lose coverage.
- **Resize factor** *(s)* sets the **output resolution** of the exported calibration — 1, 0.75, 0.5, ⅓ or 0.25 — for downstream tools that work on images resized with `cv2.resize`. It changes neither what is recorded nor how it is calibrated: the **full field of view** is kept, and accuracy is unaffected.

So the resize factor is the resolution your downstream pipeline works at; the only lever on the cost of capture and calibration is the native mode, with its field-of-view trade-off.

Whichever you choose, the intrinsics are calibrated on the native frames, then reported at the output resolution `round(native × s)`. The stored **K** corresponds to that resolution, mapped the way `cv2.resize` maps pixel centres onto that declared size (`c' = s · (c + 0.5) − 0.5` on each axis, with the factor of the rounded size), so it projects exactly onto images resized to it. The distortion coefficients do not depend on the resolution.

→ Reference: [Calibration output files](/docs/reference/output-calibration-files)
