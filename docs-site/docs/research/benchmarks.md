---
sidebar_position: 2
description: "How realtime-calib's accuracy is measured and published: reprojection-error metrics, datasets and exact commits so every number can be reproduced."
keywords: [camera calibration accuracy, calibration benchmark, reprojection error]
---

# Accuracy & benchmarks

Reproducible accuracy numbers for realtime-calib.

:::note Work in progress
Scaffold — benchmark methodology and results to be published here, including
reprojection-error distributions and comparisons against Caliscope on shared
datasets.
:::

## Planned metrics

- Per-view and aggregate **reprojection error** (intrinsics).
- Extrinsic consistency after bundle adjustment.
- Runtime / latency of the real-time path.

## Reproducibility

Each published number will link to the dataset, the exact commit/tag and the
command used to produce it, so results can be independently reproduced.
