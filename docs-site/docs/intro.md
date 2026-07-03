---
sidebar_position: 1
slug: /intro
title: Introduction
---

# What is realtime-calib?

**realtime-calib** is a local, real-time multi-camera calibration application. It
recovers both the **intrinsics** (focal length, distortion) and the **extrinsics**
(6-DoF position and orientation) of a rig of USB cameras, with live feedback, and
exports configuration files that are **compatible with
[Caliscope](https://github.com/mprib/caliscope)**.

An operator starts the project (Docker or `uv`), opens the web app (desktop,
**tablet** or mobile), and follows a wizard:

1. **Configure cameras** — discover and set up the USB cameras.
2. **Define the board(s)** — ChArUco / ArUco / chessboard.
3. **Intrinsic calibration** — camera by camera.
4. **Extrinsic calibration** — solve the whole capture volume.
5. **3D review** — inspect the reconstructed rig.
6. **Export** — Caliscope-compatible TOML + aniposelib.

Everything happens in real time: camera streams are published over **LiveKit**,
and detection/quality overlays are burned in server-side and streamed live.

## Who is this for?

- **Operators & users** setting up a multi-camera rig for motion capture,
  volumetric capture or photogrammetry.
- **Researchers** who need reproducible, inspectable calibration and a path to
  cite the method — see [Research](/docs/research/methodology).

## How it relates to Caliscope

realtime-calib **reimplements** Caliscope's calibration logic (it is not a
dependency) to make it real-time and incremental, while staying
**output-compatible**. Caliscope remains the conceptual reference for board
definitions, `calibrateCameraCharucoExtended`, PnP/stereo extrinsic
initialization and bundle adjustment.

:::tip Next step
Head to [Installation](/docs/getting-started/installation), then run your
[first calibration](/docs/getting-started/quickstart).
:::
