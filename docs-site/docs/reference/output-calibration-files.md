---
sidebar_position: 1
title: "Calibration output files — TOML & JSON reference"
sidebar_label: Calibration output files
description: "Field-by-field reference of exported calibration files: camera matrix, distortion, rotation, translation and reprojection error — Caliscope TOML, aniposelib TOML, OpenCV JSON and engine JSON."
keywords: [camera matrix, distortion coefficients, camera calibration TOML, calibration file format, reprojection error]
---

import ConventionsImg from '@site/static/img/coordinate_conventions.png';
import SoftwaresImg from '@site/static/img/coordinate_conventions_softwares.png';

# Calibration output files

When you export, realtime-calib writes **one file per selected target**. Every target carries the same calibration — the same intrinsics and 6-DoF poses — in a different shape and coordinate convention. Everything lands in the session folder, which is the source of truth for a run.

## Files written

| File | Format | For |
| --- | --- | --- |
| `camera_array.toml` | Caliscope-native TOML | Caliscope (v0.7 and later) |
| `camera_array_aniposelib.toml` | aniposelib TOML | Pose2Sim, anipose, and Caliscope ≤ 0.5.4 projects |
| `camera_array_opencv.json` | Versioned JSON contract | OpenCV pipelines |
| `camera_array_threejs.json` | Engine JSON | three.js / OpenGL |
| `camera_array_blender.json` | Engine JSON | Blender / ROS |
| `camera_array_unity.json` | Engine JSON | Unity |
| `camera_array_unreal.json` | Engine JSON | Unreal |
| `checks.json` | JSON | The [pre-export checks](/docs/guides/export#pre-export-checks), always included |

You pick which targets to export (any subset) and the length unit (mm or m). The unit applies to the aniposelib TOML and the engine JSON files; `camera_array.toml` and `camera_array_opencv.json` are always in metres. The download is one archive, `calibration_export.zip`, holding only the current selection. The **session folder** also holds the recordings, board config and computed results.

In every file, the intrinsics refer to the **output resolution** (native × resize factor), with **K** mapped at pixel centres the way `cv2.resize` maps them (see [Configure cameras](/docs/guides/configure-cameras#resolution-vs-resize-factor)), and every error is in pixels at that resolution.

## Coordinate conventions

Each engine JSON is written in its target's world convention — a combination of **up axis** (Y or Z) and **handedness** (left or right):

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

Both TOMLs keep the solver's native world: OpenCV axes (right-handed, Y-down, Z-forward). The OpenCV JSON uses the three.js world (Y-up, right-handed), so both of them place the cameras identically.

## Caliscope TOML (`camera_array.toml`)

The file Caliscope itself writes and loads: one `[cameras.N]` table per camera, keyed by the camera index. Native field semantics are preserved; project-specific fields are strictly additive, and Caliscope ignores them.

| Field | Meaning |
| --- | --- |
| `cam_id` | Camera index (the table key) |
| `size` | Image size `[width, height]` the intrinsics refer to (the export resolution) |
| `rotation_count` | Sensor rotation in quarter turns — always `0` |
| `error` | Intrinsic reprojection error (RMS, px) |
| `matrix` | 3×3 intrinsic matrix |
| `distortions` | Distortion coefficients — 5, OpenCV classic model `[k1, k2, p1, p2, k3]` |
| `translation` | Extrinsic translation (world→camera), **always in metres** |
| `rotation` | Extrinsic rotation, Rodrigues vector (world→camera) |
| `grid_count` | Number of board views (keyframes) used for the intrinsic solve |
| `fisheye` | Lens model flag — always `false` |

Additive extensions: `name` (operator label) and `device_path` (stable V4L identifier).

```toml
[cameras.0]
cam_id = 0
size = [ 1920, 1080 ]
rotation_count = 0
error = 0.21
matrix = [ [ 1000.0, 0.0, 960.0 ], [ 0.0, 1000.0, 540.0 ], [ 0.0, 0.0, 1.0 ] ]
distortions = [ 0.0, 0.0, 0.0, 0.0, 0.0 ]
translation = [ 0.0, 0.0, 0.0 ]
rotation = [ 0.0, 0.0, 0.0 ]
grid_count = 24
fisheye = false
name = "cam_0"
device_path = "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:1:1.0-video-index0"
```

Files exported by earlier realtime-calib versions used top-level `[cam_N]` tables instead, which recent Caliscope versions read as an empty camera array: export them again.

## aniposelib TOML (`camera_array_aniposelib.toml`)

The layout aniposelib reads (`CameraGroup.load`), used by Pose2Sim and anipose: one top-level `[cam_N]` table per camera, plus a `[metadata]` table.

| Field | Meaning |
| --- | --- |
| `name`, `size`, `matrix`, `distortions`, `rotation`, `fisheye` | As in `camera_array.toml` |
| `translation` | Extrinsic translation (world→camera), in the selected export units |
| `port`, `rotation_count`, `error`, `grid_count` | What Caliscope ≤ 0.5.4 reads from its `config.toml` |
| `device_path` | Additive extension (stable V4L identifier) |

`[metadata]` holds `adjusted = false`: the poses were not refined by anipose's own bundle adjustment.

Exported in metres, the `[cam_N]` tables paste unchanged into the `config.toml` of a Caliscope ≤ 0.5.4 project. Exported in millimetres, that project would read a world 1000 times too large.

## OpenCV JSON (`camera_array_opencv.json`)

A versioned contract (`"format": "realtime-calib/opencv-cameras"`, `"version": 1`) for pipelines that project with OpenCV: the poses load as they are, with no axis conversion. Within a version, each field keeps one meaning: a reader ignores unknown fields, an addition keeps the version, a change of meaning bumps it.

- **`convention`**: the pose formula `x_cam = R · x_world + t`, the camera axes (OpenCV: x right, y down, z forward), the world (right-handed, Y up, metres), and the basis from the solver's world.
- **`world`**: where the world comes from. `frame` is `target` (framed on a board), `reference` (aligned on a previous calibration, with an `alignment` block), `anchor_camera` (the anchor still at the origin) or `unknown`; `origin` describes it in words. `up = "y"` is present only when verified: the framed target level within 1°, printed face up, every camera above it. `target_offset_m` says how far a Minimize moved the framed target off the origin.
- **`source`**: the session, the export time and the service version.
- **`cameras`**, sorted by `port`:

| Field | Meaning |
| --- | --- |
| `port` | Camera index in the session (an order, not an identity) |
| `name` | Operator label |
| `device_path` | The camera's identity (stable V4L path); `null` for an imported session |
| `resolution` | `[width, height]` the intrinsics refer to (the export resolution) |
| `K` | 3×3 intrinsic matrix |
| `distCoef` | `[k1, k2, p1, p2, k3]` |
| `R` | 3×3 rotation, world → camera |
| `t` | 3×1 translation column, world → camera, **always in metres** |
| `intrinsic_error_px` | Intrinsic reprojection error (RMS, output px) |
| `extrinsic_error_px` | Extrinsic reprojection error of this camera (output px), never mixed with the intrinsic one |

This is also the file to keep as a **reference** for [aligning a later calibration](/docs/guides/extrinsic-calibration#keeping-the-rooms-frame-across-recalibrations) on the same room.

## Engine JSON (`camera_array_<target>.json`)

A self-describing document: a top-level `convention` block (up axis, handedness, axis `mapping`, `camera_forward` / `camera_up`), the `world_units`, the `anchor` camera name, and a `cameras` array.

Each camera carries a **scene form** — what a scene graph applies to place the camera object:

| Field | Meaning |
| --- | --- |
| `position` | Camera position in world units (mm or m) |
| `quaternion` | Camera orientation `[x, y, z, w]` (camera→world) |
| `matrix` | 4×4 camera→world transform |
| `intrinsics` | `{ resolution, matrix, distortions, fov_deg }` |
| `error` | The camera's extrinsic reprojection error (output px), or its intrinsic one when the array is not solved |
| `name`, `device_path` | As in the TOMLs |

**Right-handed** targets (three.js, Blender) additionally carry a **view form** — the OpenCV-style extrinsic for projection (`x_cam = R · x_world + t`):

| Field | Meaning |
| --- | --- |
| `view.R` | 3×3 rotation, world→camera |
| `view.t` | translation vector, world→camera (world units) |

**Left-handed** targets (Unity, Unreal) omit the view form: their world basis includes a mirror (`det = −1`), which would make the view rotation improper (a reflection, not a rotation) — project through the engine's own camera API instead.
