---
sidebar_position: 2
title: "Define a calibration board (ChArUco / ArUco)"
sidebar_label: Define a calibration board
description: "Configure the ChArUco board or single ArUco marker realtime-calib detects, download it to print, and enter its real measured size for an accurate metric scale."
keywords: [ChArUco board, ArUco board, calibration board, calibration target, ChArUco multi-camera calibration]
---

# Define a calibration board

**Target Config** is the first wizard step: configure the board(s) realtime-calib will detect, download them to print, then enter the measured size of the extrinsic target. Each board is saved with its own **Save** button, and Camera Setup unlocks once both are saved.

## Two roles: intrinsic and extrinsic

The screen has two tabs, **Intrinsic** and **Extrinsic**, one per role:

- The **intrinsic board** must be a **ChArUco** board: the intrinsic solve needs its many identified chessboard corners.
- The **extrinsic board** links the cameras into one world. By default it **inherits** the intrinsic board's geometry. Tick **"Use a different board for extrinsic"** to define a distinct target: either another ChArUco board or a **single ArUco marker**, which stays detectable from across a large room where the squares of a printable ChArUco board would be too small.

When the extrinsic board inherits, it follows the intrinsic board: editing the intrinsic grid updates it, and keeps the size you measured.

Save the intrinsic board first: the **Extrinsic** tab stays locked until it is saved. Above the **Save** button, a status line tells you where the current tab stands: saved, edited since the last save, or not saved yet. The next steps always use the saved boards, never unsaved edits, and **Save** is disabled while nothing has changed.

## Geometry (renders the printable PNG)

- **Type**: ChArUco (a grid) or ArUco (a single marker).
- **Dictionary**: an OpenCV predefined ArUco dictionary (default `DICT_4X4_100`). A 4×4 bit grid reads from farther away than 5×5 or 6×6; a smaller dictionary keeps more distance between valid patterns.
- **Columns × rows** (ChArUco): the grid size, default 7 × 9.
- **Marker ratio** (ChArUco): the ArUco marker inside each white square, as a fraction of the square (default 0.75).
- **Marker ID** (ArUco): which marker of the dictionary to print.
- **Inverted (ink saving)**: prints the board in negative (white on black). Inverted boards are detected like normal ones.
- **Legacy layout (OpenCV < 4.6)** (ChArUco): OpenCV 4.6 changed the layout of boards with an even number of rows. Turn this on for a board printed by an older tool, which would otherwise detect no corner.

A live preview updates as you edit, and you **Download the PNG** to print it. The preview and the download come from the same server-side render engine used for detection.

For a single marker, **keep the white margin around it** when you trim the print: the corner refinement reads the marker's black border against that margin, and refuses views where the margin is too thin.

## Metric scale (measured after printing)

The physical scale does **not** come from the render: it comes from **measuring your printed target**. After printing, measure a printed square (ChArUco) or the marker side (ArUco) with a caliper, and enter that value in millimetres on the **extrinsic** board. The intrinsic solve is scale-free and does not need it.

That measurement is what puts the extrinsic translations into real-world units, and it carries straight through: a print 2 % off makes every distance the rig reports 2 % off. No internal quality check can see this, since the whole solve scales together; the tape-measured distances of a [site template](/docs/guides/export#site-template) can.

## Changing the target after a solve

A target change does not silently rescale or invalidate a finished calibration:

- **A new measurement of the same target** keeps the extrinsic solve; the exported translations follow the new size.
- **A new geometry** for the extrinsic target (type, dictionary, grid, ratio, marker ID, inverted, legacy layout) makes the solved array obsolete. The web app asks for confirmation first; confirming discards the solve but keeps the recorded sweep, so the Extrinsic step offers **"Recompute from the recorded sweep"**.

→ See also: [Calibration best practices](/docs/reference/calibration-best-practices): board type, dictionary and geometry.
