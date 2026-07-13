<p align="center">
  <img src="https://raw.githubusercontent.com/hans-brgs/realtime-calib/main/docs-site/static/img/logo.png" alt="realtime-calib logo" width="110">
</p>

<h1 align="center">realtime-calib</h1>

<p align="center">
  <strong>Local, real-time multi-camera calibration</strong> — intrinsics (focal
  length, distortion) and 6-DoF extrinsics for a rig of USB cameras, with live
  feedback and Caliscope-compatible exports.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: AGPL-3.0" src="https://img.shields.io/badge/license-AGPL--3.0-blue.svg"></a>
  <a href="https://github.com/hans-brgs/realtime-calib/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/hans-brgs/realtime-calib?display_name=tag&color=8b5cf6"></a>
  <img alt="Platform: Linux" src="https://img.shields.io/badge/platform-Linux-1793D1?logo=linux&logoColor=white">
  <img alt="Runs in Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <a href="https://realtime-calib.hans-brgs.dev"><img alt="Documentation" src="https://img.shields.io/badge/docs-online-8b5cf6"></a>
  <a href="https://github.com/hans-brgs/realtime-calib/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/hans-brgs/realtime-calib?style=social"></a>
</p>

An operator starts the project, opens the webapp (desktop or **tablet** in
landscape today — phone &amp; portrait coming soon) and follows a wizard: camera
config → board(s) → per-camera intrinsic calibration → extrinsic calibration →
3D review → export.

https://github.com/user-attachments/assets/757728c1-5a39-4f21-b288-5ca7d26c1a18

> If the video doesn't play inline on GitHub, watch it on the
> [project site](https://realtime-calib.hans-brgs.dev).

> Inspired by [Caliscope](https://github.com/mprib/caliscope) (calibration logic,
> reimplemented — not a dependency) and the Inmersiv vision-services ecosystem
> (real-time architecture: LiveKit, multiprocessing, React/R3F webapp).

## Services

| Service | Role | Stack |
| --- | --- | --- |
| `calibration-service/` | Capture + board detection + burn-in + LiveKit publishing + computation + HTTP API + session state | Python, `uv`, multiprocessing, asyncio, OpenCV, scipy, livekit |
| `calibration-webapp/` | Operator wizard + 3D view | React, TypeScript, Vite, Mantine, Redux Toolkit, R3F/drei |
| `livekit-token-server/` | LiveKit JWT token issuance | Python (Flask) |
| `caddy/` | Reverse proxy + TLS termination + static serving | Caddy v2 |

Orchestration lives in `docker-compose.yml` (which also adds `livekit`, the
upstream WebRTC SFU). **Single stack**: Caddy (TLS) is the mandatory, always-on
entry point — tablet access via `https://<HOST_IP>`, same-machine via
`https://localhost` (one mkcert certificate covers both). See ADR-0014.

## Quick start

```bash
# Prerequisites: Docker, mkcert (LAN TLS), uv
cp .env.example .env          # fill in HOST_IP and the LiveKit keys

# Single stack (Caddy + TLS, always on)
# Tablet: https://<HOST_IP>  ·  same-machine: https://localhost
docker compose up --build
```

Then open the webapp (`https://<HOST_IP>` on the tablet, or `https://localhost`).

### First access from a tablet or phone: "Your connection is not private"

The stack serves HTTPS over your LAN with a locally-generated certificate
(mkcert). The **host machine** trusts it, but **other devices** don't know that
local certificate authority yet — so on first access from a tablet or phone the
browser warns: **"Your connection is not private"**
(`NET::ERR_CERT_AUTHORITY_INVALID`).

This is expected and safe on your own network: the connection is still
encrypted; the warning is about *who issued* the certificate, not a real
interception. To continue:

- **Chrome / Edge / Android:** tap **Advanced**, then **Proceed to `<HOST_IP>` (unsafe)**.
- **Safari / iOS:** tap **Show Details**, then **visit this website**.

To remove the warning for good, install the mkcert root CA on the device.

<p align="center">
  <img src="https://raw.githubusercontent.com/hans-brgs/realtime-calib/main/docs-site/static/img/cert-warning.gif" alt="Bypassing the browser TLS warning: Advanced, then Proceed" width="480">
</p>

## Documentation

Documentation (ADRs, entity and feature specs, roadmap) lives in the separate
`realtime-calib-doc/` repository (Obsidian vault). Development follows a
**spec-first / plan-review-implement / systematic-ADR** workflow described in the
`CLAUDE.md` files (root and per service).

## Transparency & acknowledgements

- Inspired by [Caliscope](https://github.com/mprib/caliscope), created by Mac Prible.
- I use Claude Code (Opus 4.8) to assist me in writing the code.
