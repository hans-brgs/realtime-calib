---
sidebar_position: 3
title: "Camera calibration glossary"
sidebar_label: Glossary
description: "Plain-language definitions of camera-calibration terms: intrinsics, extrinsics, ChArUco, reprojection error, bundle adjustment, triangulation and more."
keywords: [camera calibration glossary, intrinsics vs extrinsics, what is reprojection error, what is a ChArUco board, bundle adjustment definition]
---

# Camera calibration glossary

Short, plain-language definitions of the terms used across this documentation — each with a pointer to the page where it matters.

## 6-DoF pose

The position (three translations) and orientation (three rotations) of a rigid body in space — six degrees of freedom in total. Extrinsic calibration recovers one 6-DoF pose per camera.
*See: [Extrinsic calibration](/docs/guides/extrinsic-calibration).*

## Anchor camera

The camera whose pose is **fixed as the origin** of the shared coordinate frame. Fixing one camera removes the gauge freedom (the whole rig could otherwise translate/rotate freely without changing the reprojection error). In realtime-calib the anchor is the first camera in the Camera Setup order (index 0), and the world frame can later be rebased onto a board.
*See: [Configure cameras](/docs/guides/configure-cameras).*

## ArUco marker & dictionary

An **ArUco marker** is a square fiducial with a binary pattern encoding an ID. A **dictionary** is the family of valid marker patterns (e.g. `DICT_5X5_100`); smaller dictionaries keep more distance between patterns, which lowers false detections.
*See: [Define a calibration board](/docs/guides/calibration-board).*

## Bundle adjustment

The final joint optimization: a non-linear least-squares solve that refines **all camera poses and 3D points together** by minimizing the total reprojection error. realtime-calib runs it with `scipy.optimize.least_squares` (trf, sparse Jacobian), a linear pass then a robust `soft_l1` pass, keeping the anchor fixed and holding the reconstructed target to its printed geometry (see *Board rigidity*).
*See: [Methodology](/docs/research/methodology).*

## Board rigidity

How far the reconstructed calibration target deviates from its printed geometry, as an RMS of corner-to-corner distances in millimetres. The bundle adjustment is constrained to keep the target rigid; the reported rigidity is an independent judge of the extrinsic solve, since a solve can lower its reprojection error by bending the board but not this number.
*See: [Extrinsic calibration](/docs/guides/extrinsic-calibration).*

## Camera extrinsics

Where a camera **is**: the rotation and translation relating the camera to the world (or to another camera). Extrinsics change whenever the camera moves; they are solved per rig, not per camera.
*See: [Extrinsic calibration](/docs/guides/extrinsic-calibration).*

## Camera intrinsics

How a camera **projects**: focal length and principal point (the camera matrix **K**), estimated together with lens distortion. Intrinsics belong to the camera + lens + resolution combination and are independent of where the camera stands.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## ChArUco board

A hybrid calibration target: a chessboard whose white squares carry ArUco markers. The markers **identify** each corner (robust to occlusion and partial views, no rotation ambiguity) while the chessboard corners provide **subpixel accuracy**. The recommended board type in realtime-calib.
*See: [Define a calibration board](/docs/guides/calibration-board).*

## Co-visibility graph

A graph whose nodes are cameras and whose edges connect pairs that observed the board **at the same instants** often enough. Extrinsic chaining walks this graph from the anchor along the lowest-accumulated-error path.
*See: [Methodology](/docs/research/methodology).*

## Coordinate convention

The combination of **up axis** (Y or Z) and **handedness** (left or right) that defines a target's world frame — e.g. Unity is Y-up left-handed, Blender and ROS are Z-up right-handed. Exports remap axes per target so you don't do that 3D math by hand.
*See: [Calibration output files](/docs/reference/output-calibration-files).*

## Keyframe

One of the small, deliberately **diverse** subset of captured detections that the intrinsic solver actually uses. Selection combines a sampling stride, structural gates (corner count, spread), farthest-point sampling over board tilt and image position to decide *where* keyframes are wanted, and sharpness to pick the best frame at each spot.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## Lens distortion

The deviation of a real lens from the ideal pinhole model — straight lines bowing (radial distortion) or shifting (tangential). realtime-calib estimates the classic **5-coefficient model** (`k1, k2, p1, p2, k3`) used by Caliscope.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## Metric scale

What turns a relative geometry into real-world units. It comes from **measuring the printed extrinsic target** (a square edge or the marker side, with calipers), not from the nominal print size, since printers rescale. A wrong measurement rescales the whole rig uniformly; only an external reference, such as tape-measured distances in a site template, can reveal it.
*See: [Define a calibration board](/docs/guides/calibration-board).*

## Output resolution

The image size the exported calibration refers to: the native capture resolution times the **resize factor** chosen in Camera Setup. Calibration always runs on native frames; the camera matrix is then mapped to the output resolution the way `cv2.resize` maps pixels, and every reported error is in pixels at that resolution.
*See: [Configure cameras](/docs/guides/configure-cameras).*

## Projection uncertainty

How far a solved intrinsic model could misplace the ray of a pixel (1 σ, in pixels), computed from the solve's own covariance and mapped across the image. It is low where the board went and grows where it never did, where the model extrapolates.
*See: [Intrinsic calibration](/docs/guides/intrinsic-calibration).*

## Reprojection error

The distance, in pixels, between a detected board corner and where the calibrated model **re-projects** it. Usually reported as an RMS across detections, at the output resolution in realtime-calib. Lower is better, but a low number with poor frame coverage can still hide a bad calibration.
*See: [Calibration best practices](/docs/reference/calibration-best-practices).*

## Session

The folder on the server that holds everything about one calibration run — recordings, board config, results. It is the source of truth: the web app holds no durable state and rehydrates from it.
*See: [Start or load a session](/docs/guides/start-or-load-session).*

## Site template

A JSON description of a calibration room, written by hand: which camera (by USB path) is expected at which port, bounds on each camera's position and orientation, and camera-to-camera distances measured with a tape. It adds external checks before export, the only ones that can catch a wrong scale or a swapped cable.
*See: [Export](/docs/guides/export#site-template).*

## Stereo calibration

Solving the **relative pose of a camera pair** from views of the board that both cameras share (`cv2.stereoCalibrate`). realtime-calib uses it to initialize every co-visible pair before chaining and bundle adjustment.
*See: [Methodology](/docs/research/methodology).*

## Triangulation

Recovering a **3D point** by intersecting the rays from several cameras that observed it. realtime-calib triangulates board corners (DLT over all observing rays) to build the 3D point cloud that the bundle adjustment refines.
*See: [Methodology](/docs/research/methodology).*
