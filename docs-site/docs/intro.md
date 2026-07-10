---
sidebar_position: 1
slug: /intro
title: What is realtime-calib?
sidebar_label: Introduction
description: "Local, real-time, open-source multi-camera calibration: intrinsics and 6-DoF extrinsics for USB camera rigs, live feedback, Caliscope-compatible exports."
keywords: [multi-camera calibration, real-time camera calibration, open-source camera calibration, headless camera calibration, camera intrinsics, camera extrinsics, Caliscope alternative]
---

# What is realtime-calib?

**realtime-calib** is a **local, real-time, open-source** multi-camera
calibration application. It recovers both the **intrinsics** (focal length,
distortion) and the **extrinsics** (6-DoF position and orientation) of a rig of
USB cameras, with live feedback, and exports calibration files ready for
**Caliscope** and the major 3D engines. The whole calibration is driven from a
web app, so you can run it from **any device on the same local network as the
server the cameras are plugged into** — the smartphone in your pocket, a tablet,
or a laptop.

## Why it exists

realtime-calib grew out of using [Caliscope](https://github.com/mprib/caliscope)
to calibrate multi-camera USB rigs for computer-vision projects. Caliscope does
the calibration math well, but three recurring points of friction got in the way:

- **Record-then-calibrate.** Each session meant pre-recording every camera in OBS
  through a dedicated sync plugin — which was unstable, crashed, and sometimes
  silently lost frame sync, forcing manual video re-editing before calibration
  could even start.
- **No headless path.** Some clients ran on **headless Linux VMs**, so calibrating
  meant standing up an *extra* VM with a desktop, passing the cameras through, and
  driving a GUI — just to run a calibration.
- **Export mismatch.** The output convention didn't always match the target
  project's coordinate system, so results needed hand-conversion.

realtime-calib keeps what works about Caliscope — its calibration logic — and
removes those three frictions.

## What makes it different

- **One-pass calibration.** No separate video-recording step: capture, detection,
  quality feedback and computation happen live, in a single flow. What you see is
  what gets calibrated.
- **Runs headless, driven from any device.** The service runs in Docker on the
  machine the cameras are plugged into — **no desktop or GUI required on that
  host**. The operator drives everything from a web app served over the local
  network, on **any device**: laptop, tablet or phone. This fits headless
  servers, robotics rigs, motion-capture setups and production lines.
- **Multi-format export.** One calibration, exported to the convention your target
  actually uses — Caliscope-native TOML, or engine-ready JSON with the correct
  axis and handedness for **three.js / OpenGL** (Y-up, right-handed), **Blender /
  ROS** (Z-up, right-handed), **Unity** (Y-up, left-handed) and **Unreal** (Z-up,
  left-handed). The dangerous 3D math (axis remap, left-handed mirror) is done for
  you.
- **Local & private, CPU-only.** Everything runs on your own hardware — no cloud,
  no GPU. Camera streams never leave the local network.
- **Caliscope-compatible.** The TOML output keeps Caliscope's semantics, so
  existing Caliscope pipelines keep working.

## The workflow

An operator starts the stack (Docker), opens the web app (desktop, **tablet** or
mobile), and follows a wizard:

1. **Target Config** — define the board(s): ChArUco / ArUco.
2. **Camera Setup** — discover and set up the USB cameras.
3. **Intrinsics** — calibrate each camera, one by one.
4. **Extrinsics** — solve the whole capture volume, with a **3D review** of the
   reconstructed rig as its final sub-step.
5. **Export** — Caliscope-compatible TOML and/or engine-ready JSON.

Everything happens in real time: camera streams are published over **LiveKit**,
and detection/quality overlays are burned in server-side and streamed live.

## Who is this for?

- **Operators & users** setting up a multi-camera rig for motion capture,
  volumetric capture, robotics or photogrammetry.
- **Researchers** who need reproducible, inspectable calibration and a path to
  cite the method — see [Research](/docs/research/methodology).

## How it relates to Caliscope

realtime-calib **reimplements** Caliscope's calibration logic (it is not a
dependency) to make it real-time and single-pass, while staying
**output-compatible**. Caliscope remains the conceptual reference for board
definitions, `calibrateCameraCharucoExtended`, PnP/stereo extrinsic
initialization and bundle adjustment.

:::tip Next step
Head to [Installation](/docs/getting-started/installation), then run your
[first calibration](/docs/getting-started/quickstart).
:::
