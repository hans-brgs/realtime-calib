---
slug: realtime-calib-v0-1-0
title: "realtime-calib v0.1.0 — the first release is out"
authors: [myosin]
tags: [announcement]
description: "realtime-calib v0.1.0 is here: real-time, headless multi-camera calibration you drive from your desktop or tablet, with Caliscope-compatible and engine-ready exports. See the demo and try it in five minutes."
keywords: [multi-camera calibration release, real-time camera calibration, Caliscope alternative, headless camera calibration, open source camera calibration, ChArUco calibration]
---

**realtime-calib v0.1.0 is out** — the first public release. It's real-time,
headless multi-camera calibration you drive from your desktop or tablet: recover
every camera's intrinsics and 6-DoF extrinsics in one live pass, then export in
the convention your target project actually uses.

<!-- truncate -->

<video
  src="/img/hero.mp4"
  poster="/img/hero-poster.png"
  autoPlay
  muted
  loop
  playsInline
  style={{width: '100%', borderRadius: '12px', margin: '1.5rem 0'}}
/>

## The short version

If you set up rigs of USB cameras — for motion capture, robotics, volumetric
capture or photogrammetry — calibration is the quiet first step you can't skip.
realtime-calib does it **live, in one pass, on the machine the cameras are
plugged into**, and lets you drive the whole thing from a browser on a laptop or
tablet in landscape. (The longer story of *why* I built it is
[here](/blog/why-i-built-realtime-calib).)

## What's in v0.1.0

- **One pass, live.** Capture, board detection, quality feedback and the solve
  happen in a single flow — [what you see is what gets
  calibrated](/docs/guides/intrinsic-calibration), no pre-recording.
- **Intrinsics and extrinsics.** Focal length and lens distortion per camera,
  plus each camera's position and orientation in one shared frame, with a
  [live 3D review](/docs/guides/extrinsic-calibration) before you commit.
- **Headless, browser-driven.** The service runs in Docker on the camera host —
  no desktop on that machine — and you drive it from a laptop or tablet on the
  network.
- **Local, private, CPU-only.** No cloud, no GPU; streams never leave your
  network.
- **Exports that fit.** [Caliscope-compatible TOML, or engine-ready
  JSON](/docs/guides/export) with the correct axes and handedness for Unity,
  Unreal, Blender, three.js and ROS.

## Try it in five minutes

```bash
git clone https://github.com/hans-brgs/realtime-calib
cd realtime-calib
docker compose up --build
# open https://localhost  ·  from a tablet: https://<HOST_IP>
```

Then follow [the getting-started guide](/docs/intro). If you already use
Caliscope, the TOML output keeps its semantics — here's a
[side-by-side comparison](/docs/realtime-calib-vs-caliscope).

## This is early — that's the point

v0.1.0 works end to end, but it's a **0.x** release: the config and export
formats may still move before 1.0. It's also **desktop- and tablet-first for
now** — phone and portrait layouts aren't there yet and are on the list for a
later release; on a desktop or a landscape tablet, the full flow works end to
end. What I want most right now is people running it on **their own rigs** and
telling me what breaks or what's missing.

- **Get started:** [the docs](/docs/intro)
- **Code & issues:** [GitHub](https://github.com/hans-brgs/realtime-calib)

Tell me what would make it useful for *your* setup.

:::note Transparency & acknowledgements

- Inspired by [Caliscope](https://github.com/mprib/caliscope), created by Mac Prible.
- I use Claude Code (Opus 4.8) to assist me in writing the code.

:::
