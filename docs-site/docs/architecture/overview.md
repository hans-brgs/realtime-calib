---
sidebar_position: 1
---

# Architecture overview

realtime-calib is a small set of services orchestrated with Docker Compose.

:::note Work in progress
Scaffold — a full architecture diagram (data flow, LiveKit topology, processes)
will be added here.
:::

## Services

| Service | Role | Stack |
| --- | --- | --- |
| `calibration-service` | Capture, board detection, burn-in, LiveKit publish, calibration (intrinsic / extrinsic / BA), HTTP API, session state | Python 3.12, multiprocessing, asyncio, OpenCV, SciPy, LiveKit |
| `calibration-webapp` | Operator wizard + 3D view | React, TypeScript, Vite, Mantine, Redux Toolkit, R3F/drei |
| `livekit-token-server` | Issues LiveKit JWTs | Python (Flask) |
| `caddy` | Reverse proxy, TLS termination, static | Caddy v2 |

The Compose stack also runs **LiveKit** (the WebRTC SFU). Two profiles —
`default` (Caddy + TLS, tablet access) and `local` (same-machine, no TLS).

## Real-time path

Camera frames are captured and processed per-camera, overlays are burned in
server-side, and streams plus quality data are published over LiveKit to the web
app for live feedback.

→ See the [public decision records](/docs/architecture/decisions) for the "why".
