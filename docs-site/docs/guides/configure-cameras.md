---
sidebar_position: 1
---

# Configure cameras

Discover, name and set the resolution of the USB cameras in your rig.

:::note Work in progress
Scaffold page — to be expanded with screenshots from the **Camera Setup** step.
:::

## What happens here

The `calibration-service` enumerates connected USB cameras and publishes a live
preview for each over LiveKit. In the web app's **Camera Setup** view you can:

- Confirm each camera is detected and streaming.
- Assign a stable **port / index** per camera.
- Choose the **capture resolution** (native, or a downscaled mode).

## Resolution modes

Calibration can run at the **native** resolution or at a **downscaled** one. The
chosen resolution is recorded so intrinsics stay consistent with the images they
were computed from.

→ Reference: [Configuration format](/docs/reference/configuration-format)
