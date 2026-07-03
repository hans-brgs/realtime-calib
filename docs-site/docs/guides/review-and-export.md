---
sidebar_position: 5
---

# Review in 3D & export

Inspect the reconstructed rig, then export Caliscope-compatible files.

:::note Work in progress
Scaffold page — to be expanded with the 3D review UI and export dialog.
:::

## 3D review

The web app renders the solved cameras in 3D (React Three Fiber). Use it to sanity
-check camera positions, orientations and the overall capture volume before you
export.

## Export

realtime-calib exports:

- a **per-camera TOML** with the Caliscope-native fields (`port`, `size`,
  `matrix`, `distortions`, `rotation` as Rodrigues, `translation`, `error`,
  `grid_count`), and
- an **aniposelib**-compatible output.

→ Reference: [Configuration format](/docs/reference/configuration-format) ·
[Output files](/docs/reference/output-files)
