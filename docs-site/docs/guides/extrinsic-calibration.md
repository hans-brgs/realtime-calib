---
sidebar_position: 5
---

# Extrinsic calibration

Recover the 6-DoF pose of every camera in a single shared coordinate frame.

:::note Work in progress
Scaffold page — to be expanded with anchor selection and bundle-adjustment review.
:::

## The flow

Like intrinsics, extrinsics are computed **on demand**, in four phases:

1. **Capture** — record a synchronized sweep while presenting the board so that
   **pairs of cameras** see it at the same instant.
2. **Prepare** — replay the **synchronized frame groups** (every camera's frame of
   the same instant, side by side) and tune the compute: a **sampling stride**; the
   **minimum shared sightings a camera *pair* must have** — how many synchronized
   instants two cameras must both see the board before their relative pose is
   estimated directly (below it the geometry is too weak, so that pair is instead
   linked through other cameras in the co-visibility graph); and the **maximum sync
   spread** (ms) allowed within a group.
3. **Computing** — pairwise poses, transitive chaining, then bundle adjustment.
4. **Result** — inspect the reconstructed rig in the **3D review**. The world frame
   starts on the **anchor** camera (index 0); you can **rebase it onto a board**
   ("set frame on board") — for example, a board laid on the floor becomes the world
   origin and ground plane — snap-rotate the axes by **±90°**, and optionally re-run
   the bundle adjustment ("minimize").

## Under the hood

- Pairwise relative poses via **`cv2.stereoCalibrate`** on each pair's shared board
  views.
- A **co-visibility graph** (with bridge-filling) links the cameras; poses are
  **chained transitively from the anchor** (camera index 0).
- A final **bundle adjustment** (`scipy.optimize.least_squares`, trf, sparse
  Jacobian) jointly refines every non-anchor pose and the 3D points. The **anchor
  stays fixed** (identity), which removes the gauge freedom and conditions the
  solve.

→ Explanation & sources: [Methodology](/docs/research/methodology)

→ See also: [Calibration best practices](/docs/reference/calibration-best-practices)
— shared views and multi-camera tips.
