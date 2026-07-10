---
sidebar_position: 2
title: "Define a calibration board (ChArUco / ArUco)"
sidebar_label: Define a calibration board
description: "Configure the ChArUco or ArUco board realtime-calib detects, download it to print, and enter its real measured size for an accurate metric scale."
keywords: [ChArUco board, ArUco board, calibration board, calibration target, ChArUco multi-camera calibration]
---

# Define a calibration board

Configure the board realtime-calib will detect, download it to print, then enter
its measured size. **ChArUco** is recommended (robust detection plus
checkerboard-corner accuracy).

:::note Work in progress
Scaffold page — to be expanded with screenshots of the board configuration UI.
:::

## Board types

The supported targets are **ChArUco** and **ArUco**. Detection uses OpenCV's
ArUco / ChArUco detectors (OpenCV ≥ 4.8).

## Geometry (renders the printable PNG)

The geometry defines what gets rendered and printed:

- **Type** — ChArUco (a grid) or ArUco (a single marker).
- **Dictionary** — an OpenCV predefined ArUco dictionary (e.g. `DICT_5X5_100`).
- **Columns × rows** — the ChArUco grid size.
- **Marker ratio** — the marker/square size ratio (render-only).

A live preview updates as you edit, and you **Download the PNG** to print it. The
preview and the download come from the same server-side render engine.

## Metric scale (measured after printing)

The physical scale does **not** come from the render — it comes from **measuring
your printed board**. After printing, measure a printed square (ChArUco) or the
marker side (ArUco) with a caliper and enter that value in millimetres. That
measurement is what puts extrinsic translations into real-world units.

## One board or two

By default the same board is used for intrinsics and extrinsics. A **"use a
different board for extrinsic"** option lets you define a second, distinct board
for the extrinsic step.

→ See also: [Calibration best practices](/docs/reference/calibration-best-practices)
— board type, dictionary and geometry.
