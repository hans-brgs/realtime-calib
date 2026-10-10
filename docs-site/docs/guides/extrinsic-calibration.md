---
sidebar_position: 5
title: "Extrinsic calibration — 6-DoF camera poses"
sidebar_label: Extrinsic calibration
description: "Recover the 6-DoF position and orientation of every camera in one shared frame, refine with a rigidity-constrained bundle adjustment, review the solved rig in 3D and keep the room's frame across recalibrations."
keywords: [camera extrinsic calibration, 6-DoF camera pose, multi-camera extrinsics, bundle adjustment, stereo calibration]
---

# Extrinsic calibration

Recover the 6-DoF pose of every camera in a single shared coordinate frame.

## The flow

Like intrinsics, extrinsics are computed **on demand**, from a recording, in four phases:

1. **Capture**: click **Start synchronized sweep** and move the target through the shared volume so that **pairs of cameras** see it at the same instants. Every camera records, and the **co-visibility matrix** fills as each pair accumulates joint views; tap a cell to see which pair it stands for. Move steadily rather than fast: groups where the target moved between the cameras' exposures are set aside at compute time.
2. **Prepare**: replay the **synchronized frame groups** (every camera's frame of the same instant, side by side) and tune the compute:
   - **Sampling stride (1 group / N)**: how many candidate groups the detection walks through (default 2 for a single marker, 12 for a ChArUco board, whose detection costs more);
   - **Max groups (best kept)**: the sharpest groups kept for the solve, spread over the sweep (default 240 for a single marker, which only carries 4 corners per view, 80 for ChArUco);
   - **Max sync spread (ms)**: groups whose timestamps spread wider than this are discarded.
3. **Computing**: detection on the native recordings, pairwise poses, chaining from the anchor, then bundle adjustment. The dialog announces how long the detection phase will take.
4. **Result**: read the quality figures, then inspect the reconstructed rig in the **3D review**.

If the solve is discarded later (a new target geometry, recomputed intrinsics), the recording is kept and this step offers **Recompute from the recorded sweep** next to Start synchronized sweep. Applying a new camera configuration, on the other hand, drops the sweep: its videos no longer match the cameras.

## Reading the result

All errors are in pixels at the **output resolution** (native × resize factor), the same unit as the intrinsic errors.

- **Reprojection error**: the RMS over every observation the solve used.
- **Per-camera deviation**: what tells you to re-shoot one camera rather than redo the array. Up to 0.6 px reads green, up to 1.2 px amber: hand-held reference sweeps land between 0.1 and 0.8 px. Beyond that, look for a cause of that camera's own (focus, sync, intrinsics). The anchor camera is marked.
- **Board rigidity**: how far the reconstructed target deviates from its printed geometry (RMS, mm). It does not depend on the reprojection error, so it catches a solve that lowered its residuals by bending the board. Up to 2 mm (the tolerance the solver is given) is nominal, up to 5 mm worth a second look.
- **Groups / 3D points**: how much evidence the solve stood on. A low error over 3 groups is not the same result as over 40.
- **Observations**: after a Minimize, how many observations were kept out of the total.

A warning appears if the bundle adjustment stopped at its iteration ceiling instead of converging.

## The 3D review

The view shows every camera as a frustum (its image plane tinted, a small triangle on its top edge), the target of the group selected with the slider, and its triangulated corners:

- **Navigate**: drag to rotate freely (one finger on a tablet), scroll or pinch to zoom, right-drag or two fingers to pan. **Recenter** (top-right corner) returns to the default upright view. The view is framed once per solve: scrubbing the slider or folding a panel does not move it.
- **World / Board** (top-right corner): draws either the world axes (X, Y, Z, over a floor grid once the world is framed on a target) or the axes of the target in the selected group (x, y, z).

The world frame starts on the **anchor** camera (index 0). From the world-frame controls (top-left, foldable to a corner button) you can:

- **Set frame on board**: pick a group where the target lies on the floor; the printed face of the target becomes the ground plane, and the origin goes to the marker's centre (single marker) or the board's first chessboard corner (ChArUco), so the cameras end up above the floor whatever the board type. The slider marks the framed group.
- **Snap-rotate** the axes by ±90° about x, y or z.
- **Minimize**: drop the worst 2.5 % of observations and re-fit (Caliscope's quality loop). The panel then shows the error before and after. Minimize keeps your framing; if it moves the framed target slightly off the origin, the pre-export checks report by how much.
- **Align on reference**: see below.

## Keeping the room's frame across recalibrations

Each calibration's world comes from the day's target, so a recalibration moves the origin, and anything placed in the old world (landmarks, a scene) no longer lines up. **Align on reference** re-aligns the solved rig on a previous calibration of the same room:

1. Load the previous calibration's `camera_array_opencv.json` (or the bare `{"cameras": [{"port", "R", "t"}]}` it grew from).
2. Read each mode's report: the rotation and shift it would apply, and each camera's residual, also when the fit is refused.
   - **Keep the floor** (default): a rotation about the vertical and a horizontal shift. The floor stays the one you framed; the tilt and height offset a full fit would add are reported, not applied. It needs a world framed on a level floor target.
   - **Rigid (6-DoF)**: a full rigid fit, with its angle uncertainty reported. It needs 3 cameras off one line.
3. Click **Align**. Nothing moves before that click.

Cameras are matched by their USB path (by index only when one side has none, and then flagged). A fit is refused if it needs more than 20° of rotation, leaves more than 10 cm RMS of residual (another room, or a camera moved far), or implies a scale outside 0.8 to 1.25 (a units mismatch). The scale is reported, never applied. The reference stays with the session through recomputes, and the exported world records that it follows it. A camera moved by 20 to 30 cm is not reliably detected by the fit: after moving a camera, frame on the target again or use a fresh reference.

## Under the hood

- Frames are timestamped from the camera driver's own buffer timestamps when they are plausible (otherwise the host clock) and grouped into synchronized instants, with a window just under one frame period, measured from each camera's mean recorded frame period. No hardware sync is needed.
- Single-marker corners are refined from the marker's own black border, which removes the inward bias of OpenCV's contour refinement; ChArUco corners use the chessboard corners.
- Pairwise relative poses via **`cv2.stereoCalibrate`** on each pair's shared views; a **co-visibility graph** links the cameras and poses are **chained from the anchor**.
- A **bundle adjustment** (`scipy.optimize.least_squares`) jointly refines every non-anchor pose and the 3D points, a linear pass then a robust `soft_l1` pass, with **rigidity constraints** holding the reconstructed target to its printed geometry. The anchor stays fixed.

→ Explanation & sources: [Methodology](/docs/research/methodology)

→ See also: [Calibration best practices](/docs/reference/calibration-best-practices): shared views and multi-camera tips.
