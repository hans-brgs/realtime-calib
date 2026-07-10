---
sidebar_position: 8
title: "FAQ — multi-camera calibration"
sidebar_label: FAQ
description: "Frequently asked questions about realtime-calib: headless operation, phones and tablets, GPU requirements, ChArUco boards, camera sync, engine exports and licensing."
keywords: [camera calibration FAQ, headless camera calibration, calibrate cameras without GUI, camera calibration no GPU, camera synchronization calibration]
---

import Head from '@docusaurus/Head';

export const faqJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'FAQPage',
  mainEntity: [
    {
      '@type': 'Question',
      name: 'Does realtime-calib run headless, without a GUI?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes. The service runs in Docker on the machine the cameras are plugged into — no desktop environment or GUI is needed on that host. You drive the whole calibration from a web app on any device on the same local network.',
      },
    },
    {
      '@type': 'Question',
      name: 'Can I run a calibration from a phone or a tablet?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes. The operator interface is a responsive web app served over your LAN; desktop, tablet and phone all work. The heavy computation stays on the server.',
      },
    },
    {
      '@type': 'Question',
      name: 'Do I need a GPU for camera calibration?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'No. Everything — board detection, live overlays, intrinsic and extrinsic solves, bundle adjustment — runs on CPU. No cloud either: camera streams never leave your local network.',
      },
    },
    {
      '@type': 'Question',
      name: 'Which calibration board should I use?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'It depends on the step. For intrinsic calibration, use a ChArUco board: its ArUco markers make detection robust to occlusion and partial views, while its chessboard corners give subpixel accuracy. For extrinsic calibration in a large volume (a room or a hall), a plain ArUco marker is often better: seen from across a large volume the squares of a ChArUco board become too small in the image to detect reliably, and printing a very large ChArUco board is bulky and expensive, whereas a single ArUco marker stays detectable at a distance, so a small, cheap board is enough to link the cameras. Whichever you use, measure it with calipers after printing and enter the real size to set the metric scale.',
      },
    },
    {
      '@type': 'Question',
      name: 'Can I export the calibration to Unity, Unreal, Blender, three.js or ROS?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes. Export writes one file per selected target with the axis remap and handedness already applied: three.js/OpenGL (Y-up, right-handed), Blender/ROS (Z-up, right-handed), Unity (Y-up, left-handed), Unreal (Z-up, left-handed) — plus a Caliscope-compatible TOML.',
      },
    },
    {
      '@type': 'Question',
      name: 'Is realtime-calib compatible with Caliscope?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: "Yes. The TOML export keeps Caliscope's native field semantics (project-specific fields are strictly additive), so pipelines built on Caliscope's camera_array.toml keep working unchanged.",
      },
    },
    {
      '@type': 'Question',
      name: 'Do my cameras need hardware synchronization (genlock)?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'No hardware sync is required. Frames are timestamped and grouped into synchronized instants, and you control the maximum sync spread (in milliseconds) tolerated within a group when preparing the extrinsic solve.',
      },
    },
    {
      '@type': 'Question',
      name: 'Why does the live preview look lower-quality than the resolution I selected?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'That is expected, and it does not affect your calibration. To keep the CPU encoder and the network light, the live preview streamed to your browser is downscaled to at most 960 px wide (for example 960x540 from a 1080p camera) and capped at 30 fps — it is a display stream only. Detection, recording and the calibration itself run at the full calibration resolution you selected, not at the preview size. You can confirm this by checking the resolution of the recorded videos saved in the session folder: they are written at the capture resolution, not at 960 px.',
      },
    },
    {
      '@type': 'Question',
      name: 'What operating system does the camera server need?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'A Linux host: cameras are read via V4L2 and the stack runs with Docker Compose. The operator device only needs a modern browser — any OS.',
      },
    },
    {
      '@type': 'Question',
      name: 'How accurate is the calibration?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'realtime-calib follows the same calibration lineage as Caliscope and OpenCV: ChArUco intrinsics with the 8-coefficient rational model, stereo-initialized extrinsics refined by bundle adjustment. In practice accuracy depends mostly on your capture — board quality, tilt, frame coverage. Public benchmarks are being assembled.',
      },
    },
    {
      '@type': 'Question',
      name: 'Is realtime-calib free?',
      acceptedAnswer: {
        '@type': 'Answer',
        text: 'Yes — free and open source under AGPL-3.0. If you need to embed it in a proprietary product or offer it as a closed service, a commercial license and custom development are available.',
      },
    },
  ],
};

<Head>
  <script type="application/ld+json">{JSON.stringify(faqJsonLd)}</script>
</Head>

# Frequently asked questions

## Does realtime-calib run headless, without a GUI?

Yes. The service runs in Docker on the machine the cameras are plugged into —
**no desktop environment or GUI is needed on that host**. You drive the whole
calibration from a web app on any device on the same local network. See
[Installation](/docs/getting-started/installation).

## Can I run a calibration from a phone or a tablet?

Yes. The operator interface is a **responsive web app** served over your LAN;
desktop, tablet and phone all work. The heavy computation stays on the server.

## Do I need a GPU for camera calibration?

No. Everything — board detection, live overlays, intrinsic and extrinsic
solves, bundle adjustment — runs on **CPU**. No cloud either: camera streams
never leave your local network. See the
[architecture overview](/docs/architecture/overview).

## Which calibration board should I use?

It depends on the step.

**For intrinsic calibration, use a ChArUco board.** Its ArUco markers make
detection robust to occlusion and partial views, while its interpolated
chessboard corners give subpixel accuracy — a good fit when the board fills a
fair part of the frame, one camera at a time.

**For extrinsic calibration in a large volume** (a room, a hall, a wide capture
space), a plain **ArUco marker** is often the better choice. Seen from across a
large volume, the squares of a ChArUco board become too small in the image to
detect reliably; the alternative — printing a very large ChArUco board — is
bulky and expensive. A single ArUco marker stays detectable at a distance, so a
**small, cheap board is enough** to link the cameras.

Whichever you use, **measure it with calipers after printing** and enter the
real size — that measurement sets the metric scale. See
[Define a calibration board](/docs/guides/calibration-board) and the
[best practices](/docs/reference/calibration-best-practices).

## Can I export the calibration to Unity, Unreal, Blender, three.js or ROS?

Yes. Export writes **one file per selected target** with the axis remap and
handedness already applied: three.js / OpenGL (Y-up, right-handed), Blender /
ROS (Z-up, right-handed), Unity (Y-up, left-handed), Unreal (Z-up,
left-handed) — plus a Caliscope-compatible TOML. See
[Export](/docs/guides/export).

## Is realtime-calib compatible with Caliscope?

Yes. The TOML export keeps Caliscope's native field semantics
(project-specific fields are strictly additive), so pipelines built on
Caliscope's `camera_array.toml` keep working unchanged. For a full comparison,
see [realtime-calib vs Caliscope](/docs/realtime-calib-vs-caliscope).

## Do my cameras need hardware synchronization (genlock)?

No hardware sync is required. Frames are **timestamped and grouped into
synchronized instants**, and you control the **maximum sync spread** (in
milliseconds) tolerated within a group when preparing the
[extrinsic solve](/docs/guides/extrinsic-calibration).

## Why does the live preview look lower-quality than the resolution I selected?

That is expected, and it does **not** affect your calibration. To keep the CPU
encoder and the network light, the **live preview** streamed to your browser is
downscaled to at most **960 px wide** (e.g. 960×540 from a 1080p camera) and
capped at **30 fps**. It is a display stream only.

**Detection, recording and the calibration itself run at the full calibration
resolution you selected** — not at the preview size. You can confirm it by
checking the resolution of the **recorded videos** in the session folder: they
are written at the capture resolution, not at 960 px. See
[Configure cameras](/docs/guides/configure-cameras) for how the calibration
resolution is chosen.

## What operating system does the camera server need?

A **Linux host**: cameras are read via V4L2 and the stack runs with Docker
Compose. The operator device only needs a modern browser — any OS. See
[Installation](/docs/getting-started/installation).

## How accurate is the calibration?

realtime-calib follows the same calibration lineage as Caliscope and OpenCV:
ChArUco intrinsics with the 8-coefficient rational model, stereo-initialized
extrinsics refined by bundle adjustment
([methodology and sources](/docs/research/methodology)). In practice, accuracy
depends mostly on **your capture** — board quality, tilt, frame coverage: see
the [best practices](/docs/reference/calibration-best-practices). Public
[benchmarks](/docs/research/benchmarks) are being assembled.

## Is realtime-calib free?

Yes — free and open source under **AGPL-3.0**. If you need to embed it in a
proprietary product or offer it as a closed service, a
[commercial license and custom development](/docs/open-source/license#commercial-use)
are available.
