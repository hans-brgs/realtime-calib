---
sidebar_position: 8
title: "FAQ — multi-camera calibration"
sidebar_label: FAQ
description: "Frequently asked questions about realtime-calib: headless operation, phones and tablets, GPU requirements, ChArUco boards, importing videos, camera sync, metric scale, engine exports and licensing."
keywords: [camera calibration FAQ, headless camera calibration, calibrate cameras without GUI, camera calibration no GPU, camera synchronization calibration]
---

import Head from '@docusaurus/Head';

export const faqJsonLd = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Does realtime-calib run headless, without a GUI?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. The service runs in Docker on the machine the cameras are plugged into: no desktop environment or GUI is needed on that host. You drive the whole calibration from a web app on any device on the same local network. See Installation."
      }
    },
    {
      "@type": "Question",
      "name": "Can I run a calibration from a phone or a tablet?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. The operator interface is a responsive web app served over your LAN in plain HTTP; desktop, tablet and phone all work, in landscape or portrait. The heavy computation stays on the server."
      }
    },
    {
      "@type": "Question",
      "name": "Do I need a GPU for camera calibration?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "No. Everything (board detection, live overlays, intrinsic and extrinsic solves, bundle adjustment) runs on CPU. No cloud either: camera streams never leave your local network. See the architecture overview."
      }
    },
    {
      "@type": "Question",
      "name": "Which calibration board should I use?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "It depends on the step. For intrinsic calibration, use a ChArUco board (the intrinsic solve requires one). Its ArUco markers make detection robust to occlusion and partial views, while its interpolated chessboard corners give subpixel accuracy: a good fit when the board fills a fair part of the frame, one camera at a time. For extrinsic calibration in a large volume (a room, a hall, a wide capture space), a single ArUco marker is often the better choice. Seen from across a large volume, the squares of a ChArUco board become too small in the image to detect reliably; the alternative, printing a very large ChArUco board, is bulky and expensive. A single ArUco marker stays detectable at a distance, so a small, cheap board is enough to link the cameras. Whichever you use, measure it with calipers after printing and enter the real size: that measurement sets the metric scale. See Define a calibration board and the best practices."
      }
    },
    {
      "@type": "Question",
      "name": "Can I calibrate videos I have already recorded?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. Load from files on the Dashboard imports an archive (ZIP or tar) of pre-recorded videos, named cam_<n> under intrinsics/ and optionally extrinsics/, and runs the whole wizard on them with no camera attached. A Caliscope recording drops in as it is; videos without real timestamps are synchronized the way Caliscope aligns them. See Start or load a session."
      }
    },
    {
      "@type": "Question",
      "name": "Can I export the calibration to Unity, Unreal, Blender, three.js, ROS or OpenCV?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. Export writes one file per selected target with the axis remap and handedness already applied: three.js / OpenGL (Y-up, right-handed), Blender / ROS (Z-up, right-handed), Unity (Y-up, left-handed), Unreal (Z-up, left-handed), plus a versioned OpenCV JSON that OpenCV pipelines read without conversion, Caliscope's camera_array.toml and the aniposelib TOML used by Pose2Sim and anipose. See Export."
      }
    },
    {
      "@type": "Question",
      "name": "Is realtime-calib compatible with Caliscope?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes, both ways. camera_array.toml is written in the layout Caliscope v0.11.5 loads, translations in metres, with project-specific fields strictly additive; the aniposelib export covers Pose2Sim, anipose and older Caliscope projects. And Caliscope recordings can be imported and calibrated here. For a full comparison, see realtime-calib vs Caliscope."
      }
    },
    {
      "@type": "Question",
      "name": "Do my cameras need hardware synchronization (genlock)?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "No hardware sync is required. Each frame is stamped with the camera driver's own buffer timestamp, and frames are grouped into synchronized instants within just under one frame period. Groups where the target moved between the cameras' exposures are detected and set aside, and you can tighten the maximum sync spread (in milliseconds) when preparing the extrinsic solve."
      }
    },
    {
      "@type": "Question",
      "name": "Why does the live preview look lower-quality than the resolution I selected?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "That is expected, and it does not affect your calibration. To keep the CPU encoder and the network light, the live preview streamed to your browser is downscaled to at most 960 px wide (e.g. 960×540 from a 1080p camera), and the live detection overlay is computed on that preview. It is a display stream only. Recording and the calibration itself run at the full resolution you selected: every compute re-detects the board on the native recordings. You can confirm it by checking the resolution of the recorded videos in the session folder: they are written at the capture resolution, not at 960 px. See Configure cameras for how the calibration resolution is chosen."
      }
    },
    {
      "@type": "Question",
      "name": "What operating system does the camera server need?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "A Linux host: cameras are read via V4L2 and the stack runs with Docker Compose. The operator device only needs a modern browser, on any OS. See Installation."
      }
    },
    {
      "@type": "Question",
      "name": "Can I use RTSP or IP cameras, or only USB cameras?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Right now, only USB cameras are supported for live capture: they are read via V4L2 on the Linux host. RTSP streams and IP cameras are not supported yet, though videos recorded by any camera can be imported. Live RTSP / IP-camera support is a natural extension. If you need it, open an issue or a feature request on GitHub (github.com/hans-brgs/realtime-calib/issues): demand is what drives the roadmap."
      }
    },
    {
      "@type": "Question",
      "name": "How accurate is the calibration?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "realtime-calib follows the same calibration lineage as Caliscope and OpenCV: ChArUco intrinsics with the classic 5-coefficient distortion model, stereo-initialized extrinsics refined by a bundle adjustment that holds the target to its printed geometry (methodology and sources). On recorded hand-held sweeps, the per-camera reprojection error lands between 0.1 and 0.8 px at the output resolution, with the reconstructed target rigid to under 0.5 mm. Accuracy still depends mostly on your capture (board quality, tilt, frame coverage): see the best practices. Each result comes with its own quality report, so you can judge it rather than trust it. Public benchmarks are being assembled."
      }
    },
    {
      "@type": "Question",
      "name": "How do I know the metric scale is right?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "The scale comes from the size you measured on the printed target: a print 2 % off makes every distance 2 % off, and no internal check can see it, since the whole solve scales together. To check it, write a site template with a few camera-to-camera distances measured with a tape: the export then compares the calibration's scale against them and names a distance that disagrees. See Export."
      }
    },
    {
      "@type": "Question",
      "name": "Is realtime-calib free?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes: free and open source under AGPL-3.0. If you need to embed it in a proprietary product or offer it as a closed service, a commercial license and custom development are available."
      }
    }
  ]
};

<Head>
  <script type="application/ld+json">{JSON.stringify(faqJsonLd)}</script>
</Head>

# Frequently asked questions

## Does realtime-calib run headless, without a GUI?

Yes. The service runs in Docker on the machine the cameras are plugged into: **no desktop environment or GUI is needed on that host**. You drive the whole calibration from a web app on any device on the same local network. See [Installation](/docs/getting-started/installation).

## Can I run a calibration from a phone or a tablet?

Yes. The operator interface is a **responsive web app** served over your LAN in plain HTTP; desktop, tablet and phone all work, in landscape or portrait. The heavy computation stays on the server.

## Do I need a GPU for camera calibration?

No. Everything (board detection, live overlays, intrinsic and extrinsic solves, bundle adjustment) runs on **CPU**. No cloud either: camera streams never leave your local network. See the [architecture overview](/docs/architecture/overview).

## Which calibration board should I use?

It depends on the step.

**For intrinsic calibration, use a ChArUco board** (the intrinsic solve requires one). Its ArUco markers make detection robust to occlusion and partial views, while its interpolated chessboard corners give subpixel accuracy: a good fit when the board fills a fair part of the frame, one camera at a time.

**For extrinsic calibration in a large volume** (a room, a hall, a wide capture space), a single **ArUco marker** is often the better choice. Seen from across a large volume, the squares of a ChArUco board become too small in the image to detect reliably; the alternative, printing a very large ChArUco board, is bulky and expensive. A single ArUco marker stays detectable at a distance, so a **small, cheap board is enough** to link the cameras.

Whichever you use, **measure it with calipers after printing** and enter the real size: that measurement sets the metric scale. See [Define a calibration board](/docs/guides/calibration-board) and the [best practices](/docs/reference/calibration-best-practices).

## Can I calibrate videos I have already recorded?

Yes. **Load from files** on the Dashboard imports an archive (ZIP or tar) of pre-recorded videos, named `cam_<n>` under `intrinsics/` and optionally `extrinsics/`, and runs the whole wizard on them with no camera attached. A Caliscope recording drops in as it is; videos without real timestamps are synchronized the way Caliscope aligns them. See [Start or load a session](/docs/guides/start-or-load-session#load-from-files).

## Can I export the calibration to Unity, Unreal, Blender, three.js, ROS or OpenCV?

Yes. Export writes **one file per selected target** with the axis remap and handedness already applied: three.js / OpenGL (Y-up, right-handed), Blender / ROS (Z-up, right-handed), Unity (Y-up, left-handed), Unreal (Z-up, left-handed), plus a versioned OpenCV JSON that OpenCV pipelines read without conversion, Caliscope's `camera_array.toml` and the aniposelib TOML used by Pose2Sim and anipose. See [Export](/docs/guides/export).

## Is realtime-calib compatible with Caliscope?

Yes, both ways. `camera_array.toml` is written in the layout Caliscope v0.11.5 loads, translations in metres, with project-specific fields strictly additive; the aniposelib export covers Pose2Sim, anipose and older Caliscope projects. And Caliscope recordings can be imported and calibrated here. For a full comparison, see [realtime-calib vs Caliscope](/docs/realtime-calib-vs-caliscope).

## Do my cameras need hardware synchronization (genlock)?

No hardware sync is required. Each frame is stamped with the camera driver's own buffer timestamp, and frames are **grouped into synchronized instants** within just under one frame period. Groups where the target moved between the cameras' exposures are detected and set aside, and you can tighten the **maximum sync spread** (in milliseconds) when preparing the [extrinsic solve](/docs/guides/extrinsic-calibration).

## Why does the live preview look lower-quality than the resolution I selected?

That is expected, and it does **not** affect your calibration. To keep the CPU encoder and the network light, the **live preview** streamed to your browser is downscaled to at most **960 px wide** (e.g. 960×540 from a 1080p camera), and the live detection overlay is computed on that preview. It is a display stream only.

**Recording and the calibration itself run at the full resolution you selected**: every compute re-detects the board on the native recordings. You can confirm it by checking the resolution of the **recorded videos** in the session folder: they are written at the capture resolution, not at 960 px. See [Configure cameras](/docs/guides/configure-cameras) for how the calibration resolution is chosen.

## What operating system does the camera server need?

A **Linux host**: cameras are read via V4L2 and the stack runs with Docker Compose. The operator device only needs a modern browser, on any OS. See [Installation](/docs/getting-started/installation).

## Can I use RTSP or IP cameras, or only USB cameras?

Right now, **only USB cameras are supported** for live capture: they are read via V4L2 on the Linux host. RTSP streams and IP cameras are **not supported yet**, though videos recorded by any camera can be [imported](/docs/guides/start-or-load-session#load-from-files).

Live RTSP / IP-camera support is a natural extension. If you need it, **[open an issue or a feature request on GitHub](https://github.com/hans-brgs/realtime-calib/issues)**: demand is what drives the roadmap.

## How accurate is the calibration?

realtime-calib follows the same calibration lineage as Caliscope and OpenCV: ChArUco intrinsics with the classic 5-coefficient distortion model, stereo-initialized extrinsics refined by a bundle adjustment that holds the target to its printed geometry ([methodology and sources](/docs/research/methodology)). On recorded hand-held sweeps, the per-camera reprojection error lands between 0.1 and 0.8 px at the output resolution, with the reconstructed target rigid to under 0.5 mm. Accuracy still depends mostly on **your capture** (board quality, tilt, frame coverage): see the [best practices](/docs/reference/calibration-best-practices). Each result comes with its own quality report, so you can judge it rather than trust it. Public [benchmarks](/docs/research/benchmarks) are being assembled.

## How do I know the metric scale is right?

The scale comes from the size you measured on the printed target: a print 2 % off makes every distance 2 % off, and no internal check can see it, since the whole solve scales together. To check it, write a **site template** with a few camera-to-camera distances measured with a tape: the export then compares the calibration's scale against them and names a distance that disagrees. See [Export](/docs/guides/export#site-template).

## Is realtime-calib free?

Yes: free and open source under **AGPL-3.0**. If you need to embed it in a proprietary product or offer it as a closed service, a [commercial license and custom development](/docs/open-source/license#commercial-use) are available.
