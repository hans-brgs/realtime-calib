---
sidebar_position: 3
---

# Data entities

The core data contracts shared across services.

:::note Work in progress
Scaffold — each entity below mirrors a spec in the project's `20-specs/entities/`
and will be expanded with field tables.
:::

## `Camera`

A single physical camera: index/port, resolution mode, intrinsics and, once
solved, its extrinsic pose.

## `CalibrationBoard`

A board definition: family (ChArUco / ArUco / chessboard), geometry (square and
marker counts) and physical scale.

## `CameraArrayConfig`

The whole rig: the set of cameras with their intrinsics and extrinsics, written in
the [Caliscope-compatible format](/docs/reference/configuration-format).

→ These entities are the public projection of the internal entity specs.
