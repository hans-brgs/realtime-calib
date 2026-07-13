---
slug: why-i-built-realtime-calib
title: "Why I built realtime-calib: real-time, headless multi-camera calibration"
authors: [myosin]
tags: [background]
description: "The story behind realtime-calib — a recurring freelance need to calibrate multi-camera USB rigs, the frictions of record-then-calibrate tools, and why I built a live, headless, open-source alternative."
keywords: [multi-camera calibration, real-time camera calibration, Caliscope alternative, headless camera calibration, open source camera calibration, motion capture calibration]
---

I'm a freelance computer-vision developer with a PhD in human-movement science,
and I build applied technology for health, physical activity and sports
performance. This is the story of why I ended up writing my own multi-camera
calibration tool — and why I open-sourced it.

<!-- truncate -->

## The dependency I couldn't avoid

A lot of my client work rests on the same unglamorous first step: **calibrating
a rig of USB cameras**. Before you can reconstruct a movement in 3D, track a
joint or line up several viewpoints, every camera needs its **intrinsics**
(focal length, lens distortion) and its **extrinsics** (its 6-DoF position and
orientation in one shared coordinate frame). Get calibration wrong and
everything downstream is wrong — so it kept coming back, project after project,
as the quiet dependency I couldn't skip.

## Where the existing tools rubbed

The best open-source reference I found was
[Caliscope](https://github.com/mprib/caliscope). Its calibration math is
genuinely good — good enough that realtime-calib reimplements its logic rather
than reinventing it. But the *workflow* around it kept getting in my way, in
three recurring ways:

- **Record first, calibrate later.** Each session meant pre-recording every
  camera (for me, through OBS and a sync plugin), then calibrating offline. That
  step was fragile: it crashed, and it sometimes lost frame sync silently,
  forcing manual video re-editing before I could even start.
- **No headless path.** Some of my clients run on **headless Linux VMs**.
  Calibrating there meant standing up an *extra* machine with a desktop, passing
  the cameras through, and driving a GUI — a lot of ceremony just to get a
  calibration.
- **Export mismatch.** The output convention didn't always match the target
  project's coordinate system, so results needed hand-conversion — exactly the
  kind of 3D-math bookkeeping that's easy to get subtly wrong.

None of this is Caliscope's fault; it wasn't built for my constraints. But
together, these frictions made calibration slower and more error-prone than it
needed to be.

## What I actually wanted

Not a different calculator — the same solid math, with a different *workflow*
around it:

- calibrate **live, in one pass**, with no separate recording step;
- run it **on the camera host, even headless**, and drive it from **whatever
  device I had in hand** — laptop, tablet or phone;
- and get exports that **already match** the engine or tool the project targets.

## What realtime-calib does

So I built **realtime-calib**. In one line: real-time, multi-camera calibration
you drive from your desktop or tablet.

- **One pass, live.** Capture, board detection, quality feedback and the solve
  happen in a single flow — [what you see is what gets
  calibrated](/docs/guides/intrinsic-calibration), no pre-recording.
- **Headless, browser-driven.** The service runs in Docker on the machine the
  cameras are plugged into — no desktop on that host — and you drive everything
  from a browser on a laptop or tablet on the local network. (Here's the
  [architecture](/docs/architecture/overview).)
- **Local, private, CPU-only.** No cloud, no GPU; camera streams never leave
  your network.
- **Exports that fit.** One calibration, written to the convention your target
  actually uses: Caliscope-compatible TOML, or engine-ready JSON with the
  correct axes and handedness for [Unity, Unreal, Blender, three.js and
  ROS](/docs/guides/export). The dangerous axis-remap math is done for you.

If you already use Caliscope, the TOML output keeps its semantics, so your
existing pipelines keep working — I wrote a full [side-by-side
comparison](/docs/realtime-calib-vs-caliscope) if you're weighing the two.

## Why open source

I could have kept this internal. Two things changed my mind.

First, **multi-camera setups are everywhere now** — robotics rigs,
motion-capture rooms, volumetric capture, photogrammetry, production lines. As
robotics and computer vision keep spreading, the need to calibrate several
cameras into one coordinate frame only grows. A friction-free, self-hostable
tool seemed worth sharing.

Second, **calibration is a shared problem.** The value isn't in hoarding it —
it's in making it reliable for everyone who hits the same wall I did. So
realtime-calib is open source under **AGPL-3.0** (with a commercial option for
proprietary products).

## Try it — and tell me what's missing

realtime-calib is still early, and that's exactly why I'm writing this. What I
want most right now is **people to try it on their own rigs and tell me what
breaks or what's missing**, so it can grow from one freelancer's need into
something genuinely useful.

If you set up multi-camera rigs — for mocap, robotics, volumetric or
photogrammetry — I'd love your feedback.

- **Get started:** [the docs](/docs/intro)
- **Code & issues:** [GitHub](https://github.com/hans-brgs/realtime-calib)

Tell me what would make it useful for *your* setup.

:::note Transparency & acknowledgements

- Inspired by [Caliscope](https://github.com/mprib/caliscope), created by Mac Prible.
- I use Claude Code (Opus 4.8) to assist me in writing the code.

:::
