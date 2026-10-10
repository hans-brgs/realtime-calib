---
sidebar_position: 1
description: "Create a new calibration session, import pre-recorded videos (including Caliscope recordings), or resume an existing one — a session is a folder on the server holding its recordings, board config and results."
keywords: [calibration session, multi-camera calibration workflow, import calibration videos, Caliscope recordings]
---

# Start or load a session

Every calibration lives in a **session**: a folder on the server that holds its recordings, board config and results. The wizard stays **locked until a session exists**: open the web app, land on the **Dashboard** ("Welcome to the calibration bench"), and choose one of two entry modes.

## New realtime calibration

Start the full wizard from scratch, with live capture. You give the session a **folder name** (its id): the first character must be alphanumeric, then letters, digits, `.`, `_` or `-`, and it must be unique. The folder is created under the server's sessions directory (e.g. `sessions/mocap-2026-07-07`).

The session opens at the first wizard step, **Target Config**. From there, everything you capture is recorded to that folder: each intrinsic capture and each extrinsic sweep is saved, so it can be replayed and recomputed later.

## Load from files

Calibrate videos you already have, with no camera attached: **Load from files** uploads an archive (ZIP or tar, compressed or not) of pre-recorded videos, and the whole wizard then runs on the recordings.

```text
my-session.zip
├── intrinsics/          required
│   ├── cam_0.mp4
│   └── cam_1.mp4
└── extrinsics/          optional
    ├── cam_0.mp4
    ├── cam_1.mp4
    └── timestamps.csv   optional
```

- Videos are named `cam_<number>` (`.mp4`, `.mkv`, `.mov` or `.avi`; zero-padding is fine). The number is the camera index, `cam_0` is the anchor, and it must match across both folders.
- One wrapper folder inside the archive is fine, and the singular folder names (`intrinsic/`, `extrinsic/`) of a Caliscope project are accepted too.
- **Synchronization of the extrinsic videos.** A `timestamps.csv` in Caliscope's format (`cam_id,frame_time`) with real capture times is used for pairing. Without one, or with Caliscope's synthetic `inferred_timestamps.csv` (which is detected and ignored), the videos are aligned the way Caliscope aligns bare videos.
- The videos are copied frame for frame into the session (remuxed, re-encoded only when OpenCV cannot read the original container), so frame *i* stays frame *i*, variable-frame-rate phone videos included.
- In an imported session, Camera Setup is **read-only** and shows a frame from the middle of each recording instead of a live preview. Board definition, Prepare, compute, 3D review and export work as in a live session.
- A failed import leaves no partial session behind, and the error names the file at fault.

## Resuming and switching sessions

The active session is held by the `calibration-service` and persisted on disk: the web app keeps no session state of its own, so you can close the browser, or switch from a laptop to a tablet, and pick up where you left off. Recent sessions are listed on the Dashboard; opening one switches the active session server-side and the wizard rail jumps straight to its persisted step.

A **session checklist** lists what still needs attention, for example a session recorded with an earlier version that has no extrinsic board yet and asks you to revisit Target Config. Older session files are migrated transparently when they are opened.

## One operation at a time

The server runs **one long or mutating operation at a time**: a compute, a Minimize, a recording start, a session import, a camera or board change. If you trigger another one from a second device meanwhile, it is refused with a "busy" message rather than queued. While a sweep is recording, the camera configuration and the active session cannot change.
