---
sidebar_position: 2
---

# Quickstart: your first calibration

This tutorial walks through a complete calibration end to end. It assumes the
stack is [installed and running](/docs/getting-started/installation).

:::note Work in progress
This page is a scaffold. The detailed, screenshot-driven walkthrough will be
filled in as the wizard steps reach their hi-fi passes.
:::

## 1. Configure your cameras

Open the web app and go to **Camera Setup**. The service discovers connected USB
cameras and shows a live preview for each.

→ Details: [Configure cameras](/docs/guides/configure-cameras)

## 2. Define a board

Set up the calibration board (ChArUco is recommended). The board's geometry and
scale must match the physical board you print.

→ Details: [Calibration board](/docs/guides/calibration-board)

## 3. Intrinsic calibration (camera by camera)

For each camera, move the board through the field of view. Live overlays show
coverage and per-view reprojection error until the estimate is stable.

→ Details: [Intrinsic calibration](/docs/guides/intrinsic-calibration)

## 4. Extrinsic calibration

Present the board so pairs of cameras see it simultaneously. Poses are chained
from an anchor camera and refined with bundle adjustment.

→ Details: [Extrinsic calibration](/docs/guides/extrinsic-calibration)

## 5. Review in 3D and export

Inspect the reconstructed rig in the 3D view, then export
Caliscope-compatible files.

→ Details: [Review & export](/docs/guides/review-and-export)

:::tip You're done
You now have a full intrinsic + extrinsic calibration exported in a
Caliscope-compatible format. See the
[Configuration format](/docs/reference/configuration-format) reference for the
exact fields.
:::
