---
sidebar_position: 3
title: "Camera calibration glossary"
sidebar_label: Glossary
description: "Plain-language definitions of camera-calibration terms: intrinsics, extrinsics, ChArUco, reprojection error, bundle adjustment, triangulation and more."
keywords: [camera calibration glossary, intrinsics vs extrinsics, what is reprojection error, what is a ChArUco board, bundle adjustment definition]
---

# Camera calibration glossary

Short, plain-language definitions of the terms used across this documentation —
each with a pointer to the page where it matters.

## 6-DoF pose

The position (three translations) and orientation (three rotations) of a rigid
body in space — six degrees of freedom in total. Extrinsic calibration recovers
one 6-DoF pose per camera.
*See: [Extrinsic calibration](/docs/guides/extrinsic-calibration).*

## Anchor camera

The camera whose pose is **fixed as the origin** of the shared coordinate
frame. Fixing one camera removes the gauge freedom (the whole rig could
otherwise translate/rotate freely without changing the reprojection error). In
realtime-calib the anchor is the first camera in the Camera Setup order
(index 0), and the world frame can later be rebased onto a board.
*See: [Configure cameras](/docs/guides/configure-cameras).*

## ArUco marker & dictionary

An **ArUco marker** is a square fiducial with a binary pattern encoding an ID.
A **dictionary** is the family of valid marker patterns (e.g. `DICT_5X5_100`);
smaller dictionaries keep more distance between patterns, which lowers false
detections.
*See: [Define a calibration board](/docs/guides/calibration-board).*

## Bundle adjustment

The final joint optimization: a non-linear least-squares solve that refines
**all camera poses and 3D points together** by minimizing the total
reprojection error. realtime-calib runs it with `scipy.optimize.least_squares`
(trf, sparse Jacobian), keeping the anchor fixed.
*See: [Methodology](/docs/research/methodology).*

## Camera extrinsics

Where a camera **is**: the rotation and translation relating the camera to the
world (or to another camera). Extrinsics change whenever the camera moves;
they are solved per rig, not per camera.
*See: [Extrinsic calibration](/docs/guides/extrinsic-calibration).*

## Camera intrinsics

How a camera **projects**: focal length and principal point (the camera
matrix **K**), estimated together with lens distortion. Intrinsics belong to
the camera + lens + resolution combination and are independent of where the
camera stands.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## ChArUco board

A hybrid calibration target: a chessboard whose white squares carry ArUco
markers. The markers **identify** each corner (robust to occlusion and partial
views, no rotation ambiguity) while the chessboard corners provide **subpixel
accuracy**. The recommended board type in realtime-calib.
*See: [Define a calibration board](/docs/guides/calibration-board).*

## Co-visibility graph

A graph whose nodes are cameras and whose edges connect pairs that observed
the board **at the same instants** often enough. Extrinsic chaining walks this
graph from the anchor along the lowest-accumulated-error path.
*See: [Methodology](/docs/research/methodology).*

## Coordinate convention

The combination of **up axis** (Y or Z) and **handedness** (left or right)
that defines a target's world frame — e.g. Unity is Y-up left-handed, Blender
and ROS are Z-up right-handed. Exports remap axes per target so you don't do
that 3D math by hand.
*See: [Calibration output files](/docs/reference/output-calibration-files).*

## Keyframe

One of the small, deliberately **diverse** subset of captured detections that
the solver actually uses. Selection combines a sampling stride, quality gates
(sharpness, corner count) and farthest-point sampling over board tilt and
image position.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## Lens distortion

The deviation of a real lens from the ideal pinhole model — straight lines
bowing (radial distortion) or shifting (tangential). realtime-calib estimates
the 8-coefficient **rational model** used by Caliscope.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## Metric scale

What turns a relative geometry into real-world units. It comes from
**measuring the printed board** (a square edge, with calipers) — not from the
nominal print size, since printers rescale.
*See: [Define a calibration board](/docs/guides/calibration-board).*

## Reprojection error

The distance, in pixels, between a detected board corner and where the
calibrated model **re-projects** it. Usually reported as an RMS across
detections. Lower is better, but a low number with poor frame coverage can
still hide a bad calibration.
*See: [Calibration best practices](/docs/reference/calibration-best-practices).*

## Session

The folder on the server that holds everything about one calibration run —
recordings, board config, results. It is the source of truth: the web app
holds no durable state and rehydrates from it.
*See: [Start or load a session](/docs/guides/start-or-load-session).*

## Stereo calibration

Solving the **relative pose of a camera pair** from views of the board that
both cameras share (`cv2.stereoCalibrate`). realtime-calib uses it to
initialize every co-visible pair before chaining and bundle adjustment.
*See: [Methodology](/docs/research/methodology).*

## Triangulation

Recovering a **3D point** by intersecting the rays from several cameras that
observed it. realtime-calib triangulates board corners (DLT over all
observing rays) to build the 3D point cloud that the bundle adjustment
refines.
*See: [Methodology](/docs/research/methodology).*
