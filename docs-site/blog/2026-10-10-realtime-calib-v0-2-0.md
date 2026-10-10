---
slug: realtime-calib-v0-2-0
title: "realtime-calib v0.2.0 — a calibration you can check before you hand it over"
authors: [myosin]
tags: [announcement]
description: "realtime-calib v0.2.0: a full audit of the calibration pipeline, a corrected single-marker scale, pre-export checks, a site template, an OpenCV export, re-alignment on a previous calibration, and plain HTTP on the LAN — no more certificate."
keywords: [multi-camera calibration release, camera calibration accuracy, OpenCV camera calibration export, Caliscope compatible, headless camera calibration, open source camera calibration]
---

**realtime-calib v0.2.0 is out.** Where v0.1.0 was about getting a live, one-pass calibration working end to end, this release is about **trusting the result**: the whole calibration pipeline went through an audit, the errors it found are fixed, and every calibration now comes with the checks you need before handing it to the project that will use it.

<!-- truncate -->

## The short version

- **More accurate.** Single-marker calibrations were 1.3 to 3 % too large; that bias is gone. Timestamps come from the camera driver, and groups where the target moved between cameras are set aside.
- **Checkable.** A pre-export checks panel, a target-rigidity figure in millimetres, an intrinsic uncertainty map, and a **site template** that checks cabling, placement and scale against a tape measure.
- **Easier to hand over.** A ready-to-load **OpenCV JSON** export, a `camera_array.toml` that recent Caliscope versions actually load, and **re-alignment on a previous calibration** so the room's frame does not move when you recalibrate.
- **Simpler to install.** The stack now serves **plain HTTP** on your local network: no certificate to generate, no browser warning on the tablet.

## A calibration audit

I went through the calibration code end to end, looking for anything that could make a calibration wrong while reporting success, and judged every fix on recorded sessions against something other than the displayed reprojection error: ground truth, views the solve did not use, the rigidity of the target. The main findings:

- **The single-marker world was too large.** OpenCV's contour refinement places a marker's corners 1 to 2 px inward on real footage, so the marker looked smaller than it is and every distance came out 1.3 to 3 % too long. At compute time, the corners are now refined from the marker's own black border, which removes the bias. A check that measures the marker with the solve's own corners could never have seen it: it shared the bias.
- **Moving targets polluted the groups.** Without hardware sync, the cameras do not expose together, so a target moving fast is seen at different places by the members of one group. Frames now carry the camera driver's own buffer timestamp (it was a host timestamp taken after decoding, tens of milliseconds late), and groups where the target moved too much between exposures are set aside.
- **The robust bundle adjustment did not converge** on real sweeps and was flagged "truncated". It now uses the same `soft_l1` loss as Caliscope v0.11.5 and converges in tens of iterations, and ChArUco boards get the same rigidity constraints as single markers.
- **The output intrinsics were a fraction of a pixel off** when exporting at a reduced resolution: the camera matrix is now mapped the way `cv2.resize` maps pixel centres.
- **Changing the target after a solve silently rescaled the export**, and recomputing a camera's intrinsics kept poses solved with the old ones. Both now discard the solve, after a confirmation, while keeping the recording: the Extrinsic step offers **"Recompute from the recorded sweep"**.
- **The real-time capture is sturdier**: a camera can no longer be released while a frame is being read (on the rig, that used to wedge the device until a restart), opening cameras no longer freezes the API, and only one long operation runs at a time.

On the recorded single-marker sessions, the target is now held rigid to about 0.4 mm instead of about 1 mm.

## Checks before you hand a calibration over

The **Export** screen now opens on a checks panel: the worst camera's error, the epipolar consistency of every camera pair, the rigidity of the reconstructed target, whether the world is framed on a level target, and whether every camera sits above the floor. They never block an export; they tell you what to look at.

![The Export screen of v0.2.0: the Caliscope TOML preview, the export targets, and the checks before export on a 4-camera rig: the internal checks pass, the reference and site-template checks stay unavailable until you load one](/img/export-checks.png)

All of these are computed from the solve, so none can see an error the whole solve shares, above all a mis-measured target, which rescales the whole rig without changing any internal figure. That is what the new **site template** is for: a small JSON file describing your room (which camera goes to which port, where each one sits, and a few camera-to-camera distances measured with a tape). Load it once in Settings, and every export checks the cabling, the placement and the scale against it. [How to write one](/docs/guides/export#site-template).

The results panels changed too: per-camera errors are now reported at the output resolution, the **board rigidity** is shown in millimetres next to the reprojection error, and the intrinsic results include a **projection uncertainty** map that shows where the lens model can be trusted, and where the board never went.

## Exports and recalibration

- **`camera_array_opencv.json`**: a versioned file an OpenCV pipeline reads as is: world → camera `R` and `t` in metres, intrinsics at the export resolution, intrinsic and extrinsic errors kept apart, and a `world` block saying where the world comes from. [Reference](/docs/reference/output-calibration-files#opencv-json-camera_array_opencvjson).
- **Caliscope-loadable TOML**: `camera_array.toml` now uses the layout Caliscope v0.11.5 loads, always in metres, and a new `camera_array_aniposelib.toml` serves Pose2Sim, anipose and older Caliscope projects.
- **Framing on the printed face**: framing the world on a ChArUco board laid on the floor used to put every camera under the floor. It now follows the printed face for both target types.
- **Align on reference**: load a previous calibration of the same room in the 3D review and re-align the new one on it, either keeping the floor you framed or with a full rigid fit. Fits that would align onto another room are refused. [How it works](/docs/guides/extrinsic-calibration#keeping-the-rooms-frame-across-recalibrations).

## Plain HTTP on the LAN

v0.1.0 served the web app over HTTPS with a local certificate, which meant generating it, mounting it, and tapping through a browser warning on every new tablet. The web app only *receives* video and data, which browsers allow over plain HTTP, so the certificate is gone:

```bash
git clone https://github.com/hans-brgs/realtime-calib
cd realtime-calib
docker compose up --build
# open http://localhost  ·  from a tablet: http://<HOST_IP>
```

The one thing lost: the **copy** button of the export preview needs a secure context, so it only works at `http://localhost`. Download the files from a tablet instead. The stack is meant for a trusted local network, not for exposure to the internet.

## Smaller things

- A camera that cannot be opened, or that drops out mid-capture, is now shown in error with its reason and reopened automatically.
- Boards printed in negative are detected, and ChArUco boards printed by tools older than OpenCV 4.6 can be read with the new legacy-layout switch.
- Field help moved into info popovers that work on touch screens, and the result panels say what each figure means.
- Detection is about a third cheaper, with identical results.

## Upgrading from v0.1.0

- **Rebuild Caddy and switch to `http://`**: `docker compose up -d --build caddy`, then open `http://<HOST_IP>`. In `.env`, `CADDY_HTTPS_PORT` is no longer read; set `CADDY_HTTP_PORT` only if port 80 is taken. A browser that cached a redirect to `https` may need the address retyped.
- **`camera_array.toml` changed layout.** A script that read top-level `[cam_N]` tables should read `camera_array_aniposelib.toml` or `camera_array_opencv.json` instead. Files exported by v0.1.0 are read by recent Caliscope versions as an empty camera array: export them again.
- **Recompute to compare.** Existing sessions open as they are, but a calibration computed with v0.1.0 is not comparable with a new one: single-marker distances shorten by 1.3 to 3 % (the correction), the reported errors drop, and an exported `K` moves by a fraction of a pixel at a reduced output resolution.
- **No way back.** Sessions opened by v0.2.0 are migrated to a new file version that v0.1.0 does not understand: keep a copy of the session folder if you might need to go back.

## What's next

v0.2.0 is still a **0.x** release, and I would still most like to hear from people running it on **their own rigs**: what breaks, what is missing, what the checks flag on your setup.

- **What changed in the docs:** [Quickstart](/docs/getting-started/quickstart), [Export](/docs/guides/export), [Methodology](/docs/research/methodology)
- **Code & issues:** [GitHub](https://github.com/hans-brgs/realtime-calib)

:::note Transparency & acknowledgements

- Inspired by [Caliscope](https://github.com/mprib/caliscope), created by Mac Prible, whose v0.11.5 sources were the reference for several of the fixes in this release.
- I use Claude Code (Opus 4.8) to assist me in writing the code.

:::
