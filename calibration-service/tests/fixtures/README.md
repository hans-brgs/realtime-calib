# Test fixtures

Small extracts of real recordings, kept because a synthetic stand-in would not reproduce the behaviour under test. The first two come from session `calib-07-13-2026` (4 × 1080p USB cameras, single ArUco marker id 8 of `DICT_4X4_100`, 297.6 mm), the third from session `test` (the same rig, a ChArUco 7 × 9 of `DICT_4X4_100` for the intrinsics); neither session is part of the repository.

## `aruco_contour_assert.png` (42×43 px, grayscale)

A ~6 px false-positive marker (id 17) that makes OpenCV 4.13's `CORNER_REFINE_CONTOUR` assert (`nContours.size() >= 2 in _interpolate2Dline`) with the project's detector parameters: the frame of the real-session crash, `cam_0` frame 790, where the target was fine. Its candidate binarises at the 23 px threshold window, so it still asserts with the two windows of ADR-0058. Cropped with a 16 px margin around it. Used by `test_detection.py` to exercise the crop fallback of `BoardDetector._refine_on_crop`.

## `ba_real_marker_sweep.npz` (16 KB)

The bundle-adjustment inputs of 60 consecutive groups (indices 60 to 119) of the 240 groups that `_select_quality_groups` kept on the session's sweep: chained initial poses, DLT points and the 480 normalized observations. On this data the former Huber robust pass ran to the 1000-evaluation ceiling while soft_l1 converges in a few dozen (ADR-0046). Used by `test_extrinsic_solver.py`.

Generated from the audit's detection cache (`_detect_group_frames` + `_select_quality_groups` run on the session, pickled) with:

```python
pairs = stereo_pairwise(detections[60:120], board, min_shared=TUNING.min_shared)
poses = chain_from_anchor(pairs, names, names[0])
tri = triangulate_groups(detections[60:120], poses)
np.savez_compressed(
    "ba_real_marker_sweep.npz",
    camera_order=np.array(tri.camera_order),
    poses=np.stack([poses[n] for n in tri.camera_order]),
    points3d=tri.points3d,
    obs_camera=tri.obs_camera.astype(np.int64),
    obs_point=tri.obs_point.astype(np.int64),
    obs_norm=tri.obs_norm,
    point_group=tri.point_group.astype(np.int64),
    point_corner=tri.point_corner.astype(np.int64),
    focal_median=np.float64(focal_median),
    marker_size_mm=np.float64(board.marker_size_mm),
    marker_id=np.int64(board.marker_id),
)
```

The detections predate the lot 0 detector fixes; the fixture tests the solver, not the detector.

## `intrinsic_keyframes_test_cam3.npz` (12 KB)

The 50 keyframes production selected on `cam_3`'s intrinsic sweep (stride 5, cap 50), detected at the former three threshold windows (ADR-0058 runs two; the fixture tests the solver, not the detector): 1418 ChArUco corners with their ids, the per-view counts, the image size and the board. On these views OpenCV's own initialisation lands anywhere from 1.40 to 6.5 px depending on the order of the views, the seeded solve does not (ADR-0053). Used by `test_calibration.py`.

Generated with the production pipeline, `select_keyframes` wrapped to keep its output:

```python
result = compute_intrinsic_from_video(session / "intrinsic/cam_3/capture.mkv", board, cap=50, stride=5)
np.savez_compressed(
    "intrinsic_keyframes_test_cam3.npz",
    corners=np.concatenate([d.corners.reshape(-1, 2) for d in kept]).astype(np.float32),
    ids=np.concatenate([d.ids.reshape(-1) for d in kept]).astype(np.int16),
    counts=np.array([d.count for d in kept], np.int32),
    image_size=np.array(size, np.int32),
    columns=np.int32(board.columns),
    rows=np.int32(board.rows),
    dictionary=np.array(board.dictionary),
    marker_ratio=np.float64(board.marker_ratio),
)
```
