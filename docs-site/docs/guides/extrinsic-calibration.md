---
sidebar_position: 4
---

# Extrinsic calibration

Recover the 6-DoF pose of every camera in a single shared coordinate frame.

:::note Work in progress
Scaffold page — to be expanded with anchor selection and bundle-adjustment review.
:::

## The idea

Present the board so that **pairs of cameras** see it at the same time. Pairwise
relative poses are estimated, then **chained transitively from an anchor camera**
(index 0) to place every camera in one frame. A final **bundle adjustment**
refines all poses jointly over the capture volume.

## Under the hood

- PnP / `stereoCalibrate` for pairwise relative poses
- transitive chaining from the anchor
- bundle adjustment with `scipy.least_squares`

→ Explanation & sources: [Methodology](/docs/research/methodology)
