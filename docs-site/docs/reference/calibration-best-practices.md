---
sidebar_position: 2
description: "How to capture data that yields an accurate calibration: board choice, tilt and frame coverage, lighting, working distance, and how to read reprojection error."
keywords: [camera calibration best practices, accurate camera calibration, reprojection error, calibration accuracy]
---

# Calibration best practices

How to capture data that yields an accurate calibration.

## How solid is this advice?

I tried to find a **research article or meta-analysis dedicated to which capture and
board parameters maximise camera-calibration accuracy** — one that would test them
and publish validated ranges. I did not find a study specifically on that question.

Some peer-reviewed work does give **partial direction**: Zhang (2000) shows that
board **orientation** matters (near-frontal is degenerate, ~45° is best *in
simulation*), and Muñoz-Salinas et al. (2018) show that **camera-to-marker distance
/ apparent size** matters (accuracy degrades and the pose becomes ambiguous as the
marker shrinks in the image). But neither — nor any source I found — gives **ranges
of values that maximise calibration**.

That operational specificity — how many images, what board size, which dictionary,
how much tilt — exists only on **practitioner and vendor sites (OpenCV, MATLAB,
calib.io, OKLAB) that state the numbers without citing or justifying a primary
source.** So read them as **empirical rules-of-thumb, not evidence-based
constants**: recommendations that genuinely trace to primary literature are cited;
the rest are marked with an asterisk (\*) — see the note at the end.

## At a glance

| Parameter | Typical value | Basis |
| --- | --- | --- |
| Board type | **ChArUco** | OpenCV docs |
| Grid (squares) | 5×7 … 8×11\* — wide-angle 9×7+\* | OKLAB\* |
| Square size | 15–50 mm\* (larger = longer working distance) | OKLAB\* |
| Marker/square ratio | 0.6–0.75\* (realtime-calib default 0.75) | OKLAB\* |
| Dictionary | **smallest** that supplies the markers you need (e.g. `DICT_5X5_100`) | Garrido-Jurado 2014 |
| Board fills | 30–70 % of the frame\* | OKLAB\* |
| Min. marker/square size | ≥ 8–12 px\* | OKLAB\* |
| Tilt | up to ~45–60°\* (≥ 2 non-parallel orientations) | Zhang 2000 · OKLAB\* |
| Number of images | ~15–25\* (theoretical min 3) | OKLAB\*, MATLAB\* |
| Lighting | 300–1000 lux\*, even, no glare | OKLAB\* |
| Substrate | rigid & flat (aluminium / glass) | OKLAB\* |
| Reprojection error | < 0.3 px good\* · 0.3–1.0 acceptable\* · > 1.0 investigate\* | OKLAB\* |

## Choosing a board

- **Use a ChArUco board for calibration.** OpenCV explicitly recommends it: the
  ArUco markers identify each corner (no rotation ambiguity, tolerant of occlusion
  and partial views), while the interpolated **chessboard corners** give subpixel
  accuracy. [OpenCV ChArUco docs; Garrido-Jurado et al. 2014; Romero-Ramirez et al. 2018]
- **Pick the smallest ArUco dictionary that supplies the markers you need.** A
  smaller dictionary allows a larger minimum inter-marker (Hamming) distance and
  stronger error correction — `floor((d−1)/2)` correctable bits for minimum
  distance *d* — which lowers false detections. [Garrido-Jurado et al. 2014]
- **Match geometry to the job.\*** Bigger squares for longer working distances;
  more squares (denser grid) for wide-angle/fisheye lenses that need edge data;
  keep the marker at ~60–75 % of the square. realtime-calib defaults to a **7 × 8**
  grid at **0.75** ratio. [OKLAB\*]
- **Print sharp, mount flat and rigid, then measure the real scale.** Warped or
  taped-on paper introduces systematic error — use a rigid, flat substrate. Print
  at true size and **verify the square edge with calipers**; that measurement, not
  the nominal size, sets the metric scale. [OKLAB\*]

## Capture strategy (this matters most)

- **Show the board at several orientations.** Planar calibration needs **≥ 2
  non-parallel orientations**, and **≥ 3 views** for a unique solution of all five
  intrinsics (two views only work if skew is fixed to zero). [Zhang 2000]
- **Use enough, varied views — about 15–25\*.** Accuracy improves with more views,
  the biggest gain from **2 → 3**, then diminishing returns. What matters is
  *diversity*, not count: 20 varied views beat 50 similar ones. [Zhang 2000 for the
  2→3 gain; OKLAB\*, MATLAB\*]
- **Tilt the board — roughly 45°, up to ~60°.** Near-frontal boards (~5°) are a
  **degenerate configuration**. [Zhang 2000; OKLAB\*]
- **Cover the whole frame, corners and edges included.** Distortion is strongest at
  the periphery, so across your views the board must reach every corner. [OKLAB\*,
  MATLAB\*]
- **Fix the camera and the settings.** Mount it rigidly; use **manual, fixed
  exposure and focus** (auto settings drift between shots); light evenly and avoid
  glare. [OKLAB\*]
- **Avoid motion blur.** Blurred corners lose subpixel accuracy — keep the board
  (or camera) still at each capture. [OKLAB\*]
- **(Advanced) Guided / next-best-pose capture** — choosing each pose to minimise
  the parameter-covariance trace reaches higher accuracy with fewer images than
  random capture. [Tan et al. 2025; Peng & Sturm 2019; Rojtberg & Kuijper 2018]

:::note About the Zhang numbers
The **≥ 3 views**, the **2 → 3 gain**, the **~45° optimum** and the **~5° degenerate**
threshold come from Zhang's 2000 experiment — a **synthetic Monte-Carlo simulation**
(3 images, Gaussian corner noise σ = 0.5 px) on a **plain checkerboard, not a
ChArUco board** — which explicitly does **not** model the foreshortening that
degrades real corner detection at large tilt. Treat them as well-founded directions,
not exact ChArUco constants.
:::

## Working distance & apparent size

Keep the board **large in the image** — working distance is relative to board size.
As a marker's *apparent* size (pixels) shrinks, corner error grows and a
**planar-pose ambiguity** appears: four coplanar points admit two poses related by a
reflection about the camera's line of sight — worst for small or distant planes,
planes far relative to the focal length, and wide-angle lenses at close range.
Solvers return both candidates with their reprojection errors, but when the two are
close the choice is unsafe. A **ChArUco board's many corners, solved jointly,
mitigate this**; markers should stay above **~8–12 px\***, and repeatability drops
with distance (most in depth). [Muñoz-Salinas et al. 2018; Collins & Bartoli 2014
(IPPE); Aliani et al. 2026; OKLAB\*]

## Distortion model

Our review found **no evidence-based rule** for choosing the standard
(`k1,k2,p1,p2,k3`) vs the rational 8-coefficient model — the one claim that "only
k1,k2 matter" was refuted under verification. realtime-calib uses the
**8-coefficient rational model** (following Caliscope), which OpenCV consumers accept
like the classic 5; higher-order models simply need more observations to constrain.\*

## Evaluating results

- **Read the reprojection error, but don't game it.** As a rough guide, **< 0.3 px
  is good, 0.3–1.0 px acceptable, > 1.0 px worth investigating\*** (flatness, motion
  blur, bad corners). But a *lower* per-view error from a Zhang-style decoupled fit
  is **misleading** — each board pose gets its own free extrinsics, paid for with
  extrinsic-parameter uncertainty. Coverage and a shared-parameter solution beat a
  small RMSE. [OKLAB\* for the thresholds; Petković et al. 2024 for the pitfall]
- **Validate on something known.** Undistort a test image — straight lines should be
  straight — and measure a known dimension against ground truth. [OKLAB\*]

## Multi-camera extrinsics

- **Finish with a global bundle adjustment, one pose fixed as the anchor.** Extrinsic
  parameters number `6(N+K−1)` for *N* cameras and *K* board positions once one frame
  is fixed to remove gauge freedom; the final step jointly minimises reprojection
  error across all cameras. [Petković et al. 2024]
- **Give each camera pair enough shared views**, and let ChArUco's unique corner IDs
  keep every camera referencing the same points. [Heng et al. 2013; OpenCV ChArUco docs]

realtime-calib implements this pipeline — see [Methodology](/docs/research/methodology).

## Capture checklist

- ☐ Board rigid and flat; **real square size measured with calipers**.
- ☐ Camera mounted rigidly; **manual fixed exposure and focus**.
- ☐ Even lighting (~300–1000 lux\*), no glare or reflections.
- ☐ Board fills **30–70 %** of the frame\*; markers ≥ **~8–12 px\***.
- ☐ **~15–25 varied views\***: some filling the frame, varied depths, strong tilts,
  all four corners covered, a few partial views.
- ☐ No motion blur — hold still at each capture.
- ☐ Reprojection error sub-pixel; investigate if **> 1 px\***.
- ☐ Multi-camera: enough shared views per pair; finish with bundle adjustment.

## Sources

**Primary literature**

- Zhang, Z. (2000). *A Flexible New Technique for Camera Calibration.* IEEE TPAMI 22(11) —
  [full text](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/tr98-71.pdf).
- Garrido-Jurado, S., Muñoz-Salinas, R., Madrid-Cuevas, F.J., Marín-Jiménez, M.J. (2014).
  *Automatic generation and detection of highly reliable fiducial markers under occlusion.*
  Pattern Recognition 47(6):2280–2292 —
  doi:[10.1016/j.patcog.2014.01.005](https://doi.org/10.1016/j.patcog.2014.01.005).
- Romero-Ramirez, F.J., Muñoz-Salinas, R., Medina-Carnicer, R. (2018). *Speeded up detection
  of squared fiducial markers.* Image and Vision Computing 76:38–47 —
  doi:[10.1016/j.imavis.2018.05.004](https://doi.org/10.1016/j.imavis.2018.05.004).
- Muñoz-Salinas, R., Marín-Jiménez, M.J., Yeguas-Bolívar, E., Medina-Carnicer, R. (2018).
  *Mapping and localization from planar markers.* Pattern Recognition 73:158–171 —
  doi:[10.1016/j.patcog.2017.08.010](https://doi.org/10.1016/j.patcog.2017.08.010).
- Collins, T., Bartoli, A. (2014). *Infinitesimal Plane-Based Pose Estimation (IPPE).*
  IJCV 109:252–286 — [project](https://github.com/tobycollins/IPPE).
- Petković, T. et al. (2024). *Multi-camera/projector calibration analysis.* arXiv:2410.18511 —
  [link](https://arxiv.org/abs/2410.18511).
- Heng, L., Li, B., Pollefeys, M. (2013). *CamOdoCal.* IEEE/RSJ IROS 2013 —
  [link](https://people.inf.ethz.ch/pomarc/pubs/HengIROS13.pdf).
- Aliani, C., Lorenzetto Bologna, C., Francia, P., Bocchi, L. (2026). *Optimising
  Camera–ChArUco Geometry for Motion Compensation in Standing Equine CT.* Sensors 26(4):1310 —
  doi:[10.3390/s26041310](https://doi.org/10.3390/s26041310).
- Tan et al. (2025). *Next-best-pose extrinsic calibration.* arXiv:2511.18317 —
  [link](https://arxiv.org/abs/2511.18317).

**Practitioner / vendor references** (empirical, no primary sources cited — asterisked values)

- OpenCV — [ChArUco calibration](https://docs.opencv.org/4.x/da/d13/tutorial_aruco_calibration.html)
  · [ChArUco detection](https://docs.opencv.org/4.x/df/d4a/tutorial_charuco_detection.html).
- calib.io — [Calibration Best Practices](https://calib.io/blogs/knowledge-base/calibration-best-practices).
- MATLAB — [Prepare camera and capture images](https://www.mathworks.com/help/vision/ug/prepare-camera-and-capture-images-for-camera-calibration.html).
- OKLAB — [ChArUco Calibration Boards: Complete Guide](https://www.oklab.com/blog/charuco-calibration-boards-complete-guide-to-professional-camera-calibration).

:::note On asterisked (\*) values
Asterisked values come from practitioner or vendor sites (OpenCV, MATLAB, calib.io,
OKLAB) that state them **without citing a primary peer-reviewed source**. We could
not trace them to primary literature, so treat them as **empirical rules-of-thumb**,
not evidence-based constants.
:::
