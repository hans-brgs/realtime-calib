---
sidebar_position: 2
description: "How realtime-calib's accuracy is measured and published: reprojection error, target rigidity, held-out views and scale, with datasets and exact commits so every number can be reproduced."
keywords: [camera calibration accuracy, calibration benchmark, reprojection error]
---

# Accuracy & benchmarks

Reproducible accuracy numbers for realtime-calib.

:::note Work in progress
Benchmark results will be published here, including comparisons against Caliscope on shared datasets.
:::

## What gets measured

A displayed RMSE alone is a poor judge: dropping the hardest observations lowers it, and a solve can lower it by bending the reconstructed target. Changes to the pipeline are therefore judged on recorded sessions against a judge chosen for the question:

- **Reprojection error**, per camera and overall, in pixels at a stated resolution (comparisons across tools must use the same one).
- **Target rigidity**: the reconstructed target's corner distances against the printed geometry, in millimetres. It depends on no solver parameter.
- **Held-out views**: the error on views the solve did not use, to tell a better model from a better fit.
- **Repeatability**: the spread of independent solves of the same rig.
- **Scale**: camera-to-camera distances against tape measurements, and corner positions against a ChArUco ground truth.
- **Runtime** of the live path (frames per second per camera, API latency) and of the computes.

## Reproducibility

The repository ships `calibration-service/tools/eval_extrinsic_session.py`, which re-solves a recorded session folder with the production pipeline and reports the RMSE at native and output resolution, the target rigidity in millimetres and the distances between cameras. Each published number will link to the dataset, the exact commit or tag and the command used to produce it, so results can be independently reproduced.
