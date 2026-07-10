---
sidebar_position: 1
title: "Calibration output files — TOML & JSON reference"
sidebar_label: Calibration output files
description: "Field-by-field reference of exported calibration files: camera matrix, distortion, rotation, translation and reprojection error — Caliscope TOML and engine JSON."
keywords: [camera matrix, distortion coefficients, camera calibration TOML, calibration file format, reprojection error]
---

import ConventionsImg from '@site/static/img/coordinate_conventions.png';
import SoftwaresImg from '@site/static/img/coordinate_conventions_softwares.png';

# Calibration output files

When you export, realtime-calib writes **one file per selected target**. Every
target carries the same calibration — the same intrinsics and 6-DoF poses — in a
different shape and coordinate convention. Everything lands in the session folder,
which is the source of truth for a run.

## Files written

| File | Format | For |
| --- | --- | --- |
| `camera_array.toml` | Caliscope-native TOML | Caliscope & OpenCV-style pipelines |
| `camera_array_threejs.json` | Engine JSON | three.js / OpenGL |
| `camera_array_blender.json` | Engine JSON | Blender / ROS |
| `camera_array_unity.json` | Engine JSON | Unity |
| `camera_array_unreal.json` | Engine JSON | Unreal |

You pick which targets to export (any subset) and the length unit (mm or m). The
**session folder** also holds the recordings, board config and computed results.

## Coordinate conventions

Each engine JSON is written in its target's world convention — a combination of
**up axis** (Y or Z) and **handedness** (left or right):

<figure style={{textAlign: 'center', margin: '1.75rem 0'}}>
  <img
    src={ConventionsImg}
    alt="Y-up vs Z-up crossed with left- vs right-handed axis triads"
    style={{width: '60%', height: 'auto'}}
  />
  <figcaption style={{fontSize: '0.85rem', opacity: 0.75, marginTop: '0.5rem'}}>
    <strong>Coordinate conventions.</strong> The four world conventions
    realtime-calib exports to — up axis (Y or Z) combined with handedness (left- or
    right-handed).
  </figcaption>
</figure>

Each convention maps to a target engine:

<figure style={{textAlign: 'center', margin: '1.75rem 0'}}>
  <img
    src={SoftwaresImg}
    alt="Convention grid labelled with Unity, OpenGL, Unreal, Blender and ROS"
    style={{width: '60%', height: 'auto'}}
  />
  <figcaption style={{fontSize: '0.85rem', opacity: 0.75, marginTop: '0.5rem'}}>
    <strong>Target software per convention.</strong> Unity (Y-up, left-handed),
    three.js / OpenGL (Y-up, right-handed), Unreal (Z-up, left-handed), Blender /
    ROS (Z-up, right-handed).
  </figcaption>
</figure>

The Caliscope TOML keeps OpenCV's native axes (right-handed, Y-down, Z-forward).

## Caliscope TOML (`camera_array.toml`)

Compatible with Caliscope: native field semantics are preserved, project-specific
fields are strictly additive. One `[cam_N]` table per camera.

| Field | Meaning |
| --- | --- |
| `port` | Camera index / identifier |
| `size` | Image size `[width, height]` at the calibration resolution |
| `matrix` | 3×3 intrinsic matrix |
| `distortions` | Distortion coefficients — 8 (OpenCV rational model) |
| `rotation` | Extrinsic rotation, Rodrigues vector (world→camera) |
| `translation` | Extrinsic translation, in millimetres |
| `error` | Reprojection error (RMS) |
| `grid_count` | Total board corners used across the keyframes |

Additive, non-Caliscope extensions: `name` (operator label) and `device_path`
(stable V4L identifier).

```toml
[cam_0]
port = 0
name = "cam_0"
size = [ 1920, 1080 ]
matrix = [ [ 1000.0, 0.0, 960.0 ], [ 0.0, 1000.0, 540.0 ], [ 0.0, 0.0, 1.0 ] ]
distortions = [ 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0 ]
rotation = [ 0.0, 0.0, 0.0 ]
translation = [ 0.0, 0.0, 0.0 ]
error = 0.0
grid_count = 0
```

## Engine JSON (`camera_array_<target>.json`)

A self-describing document: a top-level `convention` block (up axis, handedness,
axis `mapping`, `camera_forward` / `camera_up`), the `world_units`, the `anchor`
camera name, and a `cameras` array.

Each camera carries a **scene form** — what a scene graph applies to place the
camera object:

| Field | Meaning |
| --- | --- |
| `position` | Camera position in world units (mm or m) |
| `quaternion` | Camera orientation `[x, y, z, w]` (camera→world) |
| `matrix` | 4×4 camera→world transform |
| `intrinsics` | `{ resolution, matrix, distortions, fov_deg }` |
| `error` | Reprojection error |
| `name`, `device_path` | As in the TOML |

**Right-handed** targets (three.js, Blender) additionally carry a **view form** —
the OpenCV-style extrinsic for projection (`x_cam = R · x_world + t`):

| Field | Meaning |
| --- | --- |
| `view.R` | 3×3 rotation, world→camera |
| `view.t` | translation vector, world→camera (world units) |

**Left-handed** targets (Unity, Unreal) omit the view form: their world basis
includes a mirror (`det = −1`), which would make the view rotation improper (a
reflection, not a rotation) — project through the engine's own camera API instead.
