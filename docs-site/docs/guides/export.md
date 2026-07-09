---
sidebar_position: 6
---

# Export

Export your calibration in the convention your target needs.

:::note Work in progress
Scaffold page — to be expanded with the export dialog.
:::

Export is **convention-first**: you pick the **target software** and the length
**unit** (mm or m), and you get **one file per selected target**. The targets are
independent and equal:

- **Caliscope** — a single Caliscope-native TOML (`camera_array.toml`) with one
  `[cam_N]` table per camera: `port`, `size`, `matrix`, `distortions`, `rotation`
  (Rodrigues), `translation`, `error`, `grid_count` (plus additive `name` /
  `device_path` extensions).
- **Engine JSON** — `camera_array_<target>.json` for **three.js** (Y-up,
  right-handed), **Blender / ROS** (Z-up, right-handed), **Unity** (Y-up,
  left-handed) and **Unreal** (Z-up, left-handed), with the axis remap and
  handedness already applied.

→ Reference: [Calibration output files](/docs/reference/output-calibration-files)
