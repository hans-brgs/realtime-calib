---
sidebar_position: 1.5
title: "realtime-calib vs Caliscope"
sidebar_label: vs Caliscope
description: "An honest comparison for multi-camera calibration: Caliscope's record-then-calibrate desktop app vs realtime-calib's live, one-pass, headless web workflow — and when to use which."
keywords: [Caliscope alternative, realtime-calib vs Caliscope, multi-camera calibration software, camera calibration tool comparison, motion capture calibration]
---

# realtime-calib vs Caliscope

[Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause) is the
open-source multi-camera calibration tool this project owes the most to:
**realtime-calib reimplements Caliscope's calibration logic** — the same board
definitions, the same OpenCV solve lineage, the same bundle-adjustment shape —
and stays **output-compatible** with it. If you are looking for a *Caliscope
alternative*, this page lays out honestly how the two differ and when each one
is the better fit.

## TL;DR

- Pick **Caliscope** if you want a **desktop application** that also carries a
  basic **3D reconstruction pipeline** (ONNX pose estimation — RTMPose, SLEAP,
  DeepLabCut — and 3D trajectories as CSV/TRC), and calibrating from
  **pre-recorded, synchronized videos** fits your workflow.
- Pick **realtime-calib** if you want to **calibrate live, in one pass**, from
  a browser on **any device**, against cameras plugged into a possibly
  **headless Linux server** — and you need exports in **engine conventions**
  (Unity, Unreal, Blender, three.js, ROS) on top of a Caliscope-compatible
  TOML.

## Side by side

| | Caliscope | realtime-calib |
| --- | --- | --- |
| Workflow | **Record first, then calibrate** from synchronized footage | **One pass, live** — capture, detection, quality feedback and compute in a single flow |
| Interface | Desktop GUI (Python application) | **Web app** on the local network — desktop, tablet or phone |
| Headless camera host | Not a supported path | **Yes** — Docker on a headless Linux server, no desktop needed |
| Camera host OS | Wherever the Python desktop app runs | **Linux** (cameras read via V4L2), via Docker Compose |
| Live feedback | Offline review of recordings | Detection overlays and quality telemetry, streamed live |
| Beyond calibration | Basic reconstruction pipeline: ONNX pose estimation, 3D trajectories (CSV / TRC) | **Calibration only**, by design |
| Exports | Its `camera_array.toml` + an aniposelib export (anipose, Pose2Sim) | **Caliscope-compatible TOML** + engine JSON for three.js, Blender / ROS, Unity, Unreal |
| Calibration math | ChArUco intrinsics, pairwise stereo extrinsics, bundle adjustment | **Same lineage, reimplemented** for a real-time, single-pass flow |
| GPU | Calibration is CPU-based | Not required — CPU-only |
| License | BSD-2-Clause | AGPL-3.0, with a [commercial option](/docs/open-source/license#commercial-use) |

## When Caliscope is the better fit

- You want calibration **and** a first 3D reconstruction (landmark
  triangulation from pose-estimation models) in one desktop tool.
- Your cameras are attached to a **desktop machine you sit at**, and a
  record-then-process workflow suits you.
- You prefer a permissive **BSD-2-Clause** license.

## When realtime-calib is the better fit

- Your cameras are plugged into a **headless server** (robotics rig,
  motion-capture room, production line) and you want to drive the calibration
  from a **phone, tablet or laptop** on the same network.
- You want **live feedback while you capture** — coverage overlays and quality
  gauges — instead of discovering problems after recording.
- Your calibration must land in a **game engine or 3D tool**: the JSON exports
  carry the correct axis conventions and handedness for Unity, Unreal, Blender,
  three.js and ROS, so no hand-conversion of the 3D math.

## Compatibility between the two

realtime-calib's TOML export keeps Caliscope's native field semantics —
project-specific fields are strictly additive — so **pipelines built on
Caliscope's `camera_array.toml` keep working unchanged**. See the
[calibration output files reference](/docs/reference/output-calibration-files)
for every field.

The calibration methodology itself — what is solved, with which OpenCV calls,
and how the bundle adjustment is set up — is documented with its sources in
[Methodology](/docs/research/methodology).
