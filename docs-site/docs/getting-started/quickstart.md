---
sidebar_position: 2
description: "Your first multi-camera calibration end to end: define a ChArUco board, set up USB cameras, calibrate intrinsics and extrinsics live, then review in 3D, check and export."
keywords: [camera calibration tutorial, multi-camera calibration quickstart, ChArUco calibration, USB camera calibration]
---

# Quickstart: your first calibration

This tutorial walks through a complete calibration end to end. It assumes the stack is [installed and running](/docs/getting-started/installation). Each step links to its detailed guide.

## First access from a tablet or phone {#first-access}

Open `http://<HOST_IP>` in the device's browser (plain `http`, no certificate warning). Use the IP of the machine running the stack, the one set as `HOST_IP` in `.env`.

## 1. Start or load a session

Open the web app: you land on the **Dashboard** ("Welcome to the calibration bench"). Nothing in the wizard is reachable yet: **every step stays locked until a session exists.** Pick one of the two entry modes:

- **New realtime calibration**: start the full wizard from scratch, with live capture. Each sweep is recorded to the session folder so it can be replayed and recomputed later.
- **Load from files**: upload an archive of pre-recorded camera videos (for example a Caliscope recording) and run the same wizard on them, without any camera attached.

Once a session is created or opened, the wizard rail unlocks and follows the persisted step.

→ Details: [Start or load a session](/docs/guides/start-or-load-session)

## 2. Define a board: Target Config

Go to **Target Config**. Set up the intrinsic board: a **ChArUco** board, which the intrinsic solve requires. The extrinsic step **inherits it by default**, or you can define a distinct extrinsic target, such as a single large **ArUco marker** for a big room. Download the PNG, print it, then **measure the printed square or marker with a caliper** and enter that size on the extrinsic board: it sets the metric scale of the whole rig. **Camera Setup stays locked until the intrinsic board is defined.**

→ Details: [Define a calibration board](/docs/guides/calibration-board)

## 3. Configure your cameras: Camera Setup

Go to **Camera Setup**. The service discovers the connected USB cameras and shows a live preview for each. Pick the shared resolution and frame rate, the output resize factor, and drag the cameras into order (the first one is the extrinsic anchor). **Apply** writes the configuration; **Continue** moves on.

→ Details: [Configure cameras](/docs/guides/configure-cameras)

## 4. Intrinsic calibration (camera by camera)

For each camera, start a capture and move the board through the whole field of view, tilted, while the live overlay shows the board and its coverage. Stop, then use **Prepare** to replay the recording, **trim** it, set the **sampling stride** and the **keyframe cap**, and compute. Read the results (reprojection error, coverage, projection uncertainty) before moving to the next camera.

→ Details: [Intrinsic calibration](/docs/guides/intrinsic-calibration)

## 5. Extrinsic calibration & 3D review

Start a **synchronized sweep** and move the target through the shared volume so every camera pair sees it at the same instants; the co-visibility matrix fills as pairs accumulate joint views. Stop, then **Prepare** the compute (sampling stride, max groups, max sync spread) and solve. Read the per-camera error and the board rigidity, then **inspect the reconstructed rig in the 3D view**: frame the world on a board laid on the floor, snap-rotate the axes, optionally **Minimize**, or **align on a reference** calibration to keep the room's frame.

→ Details: [Extrinsic calibration](/docs/guides/extrinsic-calibration)

## 6. Check and export

The **Export** screen first shows the **pre-export checks**: worst camera error, epipolar consistency, target rigidity, world framing, cameras above the floor, and, with a site template, cabling, placement and scale. Then pick the length unit and the targets (Caliscope TOML, aniposelib TOML, OpenCV JSON, engine JSON for three.js, Blender / ROS, Unity, Unreal) and download.

→ Details: [Export](/docs/guides/export)

:::tip You're done
You now have a full intrinsic + extrinsic calibration, exported to the convention your target needs. See the [Calibration output files](/docs/reference/output-calibration-files) reference for the exact fields.
:::
