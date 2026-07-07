---
sidebar_position: 1
---

# Configuration format

realtime-calib reads and writes a configuration format that is **compatible with
Caliscope** (ADR-0002): the native field semantics are preserved, and any
project-specific fields are strictly additive.

:::note Work in progress
Scaffold — the full field-by-field table will be generated from the
`CameraArrayConfig` entity spec.
:::

## Per-camera fields

Each camera is written as a TOML block with the Caliscope-native fields:

| Field | Meaning |
| --- | --- |
| `port` | Camera index / identifier |
| `size` | Image size `[width, height]` used for calibration |
| `matrix` | 3×3 intrinsic matrix |
| `distortions` | Distortion coefficients (rational model) |
| `rotation` | Extrinsic rotation, **Rodrigues** vector |
| `translation` | Extrinsic translation |
| `error` | Reprojection error |
| `grid_count` | Number of boards/grids used |

```toml
[cam_0]
port = 0
size = [ 1920, 1080 ]
matrix = [ [ 1000.0, 0.0, 960.0 ], [ 0.0, 1000.0, 540.0 ], [ 0.0, 0.0, 1.0 ] ]
distortions = [ 0.0, 0.0, 0.0, 0.0, 0.0 ]
rotation = [ 0.0, 0.0, 0.0 ]
translation = [ 0.0, 0.0, 0.0 ]
error = 0.0
grid_count = 0
```

:::info Compatibility
Do not break the semantics of native fields. Project-specific fields are additive
only — see ADR-0002.
:::
