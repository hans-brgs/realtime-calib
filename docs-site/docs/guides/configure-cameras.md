---
sidebar_position: 3
description: "Detect the USB cameras, set one shared capture configuration (resolution, frame rate, resize factor) and order the rig — the first camera anchors the extrinsics."
keywords: [USB camera calibration, multi-camera rig, camera detection, multi-camera USB calibration software]
---

# Configure cameras

Detect the USB cameras, set one shared capture configuration, and order them: the first camera is the extrinsic anchor.

## What happens here

The `calibration-service` enumerates the connected USB cameras (V4L2) and publishes a live preview of each over LiveKit. In the web app's **Camera Setup** view you can:

- **Detect / re-detect** the connected cameras and confirm each is streaming.
- Set the **capture configuration**: resolution, frame rate and resize factor. These are **shared by all cameras** and only offer the modes common to every detected camera.
- **Order the cameras** by drag-and-drop. The camera at the top (**index 0**) is the **anchor** used to chain extrinsic poses.

Cameras are identified by their stable USB path (`/dev/v4l/by-path/…`), so a camera keeps its identity as long as it stays on the same port.

## Apply, then continue

Your edits (parameters **and** order) form a draft that nothing reads until you click **Apply configuration**. Apply is enabled while the draft differs from the saved configuration; **Continue to Intrinsics** is enabled once there is nothing left to apply. This way you can check that a change took effect before moving on.

Applying a changed configuration rebuilds the cameras, and a rebuild discards the calibrations already computed: their recordings were made with the previous configuration, and are keyed by camera index. The web app asks for confirmation first.

## Capture configuration

Because the rig is calibrated as one system, resolution and frame rate are picked **once and applied to every camera**: the selectors only offer modes supported by all detected cameras. The frame rate is **30 or 15 fps**, capped by each camera's native maximum for the chosen resolution. Lower rates spare the USB bus when many cameras share it.

## Resolution vs. resize factor

Two independent controls set a pixel count, and they act at different places:

- **Resolution** selects the camera's **native capture mode**: what is recorded and calibrated. A lower mode records and computes faster, but beware: **most USB cameras produce a lower resolution by cropping the sensor**, not by scaling it down, so dropping the resolution often **narrows the field of view**. You gain speed but lose coverage.
- **Resize factor** *(s)* sets the **output resolution** of the exported calibration (1, 0.75, 0.5, ⅓ or 0.25) for downstream tools that work on images resized with `cv2.resize`. It changes neither what is recorded nor how it is calibrated: the **full field of view** is kept, and accuracy is unaffected.

So the resize factor is the resolution your downstream pipeline works at; the only lever on the cost of capture and calibration is the native mode, with its field-of-view trade-off.

Whichever you choose, the intrinsics are calibrated on the native frames, then reported at the output resolution `round(native × s)`. The stored **K** corresponds to that resolution, mapped the way `cv2.resize` maps pixel centres onto that declared size (`c' = s · (c + 0.5) − 0.5` on each axis, with the factor of the rounded size), so it projects exactly onto images resized to it. The distortion coefficients do not depend on the resolution. Every reported error, intrinsic and extrinsic, is in pixels at that output resolution too.

## Camera health

Each preview tile carries a status dot that follows the camera's real state: live, opening, or in error.

- A camera that **cannot be opened**, or that **stops delivering frames** for about 3 seconds (unplugged, USB bandwidth exhausted), shows an overlay with the reason, and a banner lists the failing cameras.
- The service retries on its own, with a growing delay (1, 2, 4, 8, 16, then every 30 seconds), and the camera comes back live without reloading the page once it streams again.
- A camera that streams another resolution than the one configured is reported in error rather than taken live, since its recording would not match its calibration.

## Rig settings

The **Settings** window (from the rail) holds settings of the rig rather than of a session:

- **Recording quality (JPEG)**: the quality of the recorded videos every compute re-detects from (85 to 100, default 95).
- **Preview FPS**: the preview follows the camera's frame rate by default; a reduced preview rate spares the server's video encoder. Recording, detection and computes are never affected.
- **Site template**: see [Export](/docs/guides/export#site-template).

## Imported sessions

In a session [loaded from files](/docs/guides/start-or-load-session#load-from-files), Camera Setup is read-only: it shows the first frame of each recording, and the order comes from the file names.

→ Reference: [Calibration output files](/docs/reference/output-calibration-files)
