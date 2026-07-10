---
sidebar_position: 1
description: "Create a new calibration session or resume an existing one — a session is a folder on the server holding its recordings, board config and results."
keywords: [calibration session, multi-camera calibration workflow]
---

# Start or load a session

Every calibration lives in a **session** — a folder on the server that holds its
recordings, board config and results. The wizard stays **locked until a session
exists**: open the web app, land on the **Dashboard** ("Welcome to the calibration
bench"), and choose one of two entry modes.

## New realtime calibration

Start the full wizard from scratch. You give the session a **folder name** (its
id) — the first character must be alphanumeric, then letters, digits, `.`, `_` or
`-`, and it must be unique. The folder is created under the server's sessions
directory (e.g. `sessions/mocap-2026-07-07`).

The session opens at the first wizard step, **Target Config**. From there,
everything you capture is recorded to that folder — each intrinsic sweep is saved,
so it can be replayed and recomputed later.

## Load from files *(in development)*

The second entry mode points at an existing session folder, reads its artifacts —
recorded videos, board config, results — and derives the wizard state so you can
recompute or resume where you left off.

:::note Not wired yet
Folder inspection depends on the recording & replay backend and is not available
yet — the "Choose folder…" action is disabled. Start a realtime calibration for
now; its recordings become loadable here later.
:::

## Resuming and switching sessions

The active session is held by the `calibration-service` and persisted on disk —
the web app keeps no session state of its own. Recent sessions are listed on the
Dashboard; opening one switches the active session server-side and the wizard rail
jumps straight to its persisted step.
