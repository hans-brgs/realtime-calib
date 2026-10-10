# Test fixtures

Small extracts of real recordings, kept because a synthetic stand-in would not reproduce the behaviour under test. Both come from session `calib-07-13-2026` (4 × 1080p USB cameras, single ArUco marker id 8 of `DICT_4X4_100`, 297.6 mm), which is not part of the repository.

## `aruco_contour_assert.png` (38×39 px, grayscale)

A ~7 px false-positive marker (id 17) that makes OpenCV 4.13's `CORNER_REFINE_CONTOUR` assert (`nContours.size() >= 2 in _interpolate2Dline`) with the project's detector parameters. Cropped with a 16 px margin around that candidate from `cam_2`, frame 601 — a frame without the target. Used by `test_detection.py` to exercise the crop fallback of `BoardDetector._refine_on_crop`.

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
