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

An operator starts the project, opens the webapp on a desktop, a **tablet** or a phone (landscape or portrait) and follows a wizard: board(s) → camera setup → per-camera intrinsic calibration → extrinsic calibration → 3D review → checks and export.

https://github.com/user-attachments/assets/757728c1-5a39-4f21-b288-5ca7d26c1a18

> If the video doesn't play inline on GitHub, watch it on the
> [project site](https://realtime-calib.hans-brgs.dev).

> Inspired by [Caliscope](https://github.com/mprib/caliscope) (calibration logic, reimplemented — not a dependency) and the Inmersiv vision-services ecosystem (real-time architecture: LiveKit, React/R3F webapp).

## Services

| Service | Role | Stack |
| --- | --- | --- |
| `calibration-service/` | Capture + board detection + burn-in + LiveKit publishing + computation + HTTP API + session state | Python, `uv`, asyncio + threads, OpenCV, scipy, livekit |
| `calibration-webapp/` | Operator wizard + 3D view | React, TypeScript, Vite, Mantine, Redux Toolkit, R3F/drei |
| `livekit-token-server/` | LiveKit JWT token issuance | Python (Flask) |
| `caddy/` | Reverse proxy + static serving (plain HTTP) | Caddy v2 |

Orchestration lives in `docker-compose.yml` (which also adds `livekit`, the upstream WebRTC SFU). **Single stack**: Caddy is the mandatory, always-on entry point, in plain HTTP on the LAN — tablet access via `http://<HOST_IP>`, same-machine via `http://localhost`. No certificate to generate or trust: the webapp only receives video and data, which needs no secure context.

## Quick start

```bash
# Prerequisites: Docker (uv and Node only for local development)
git clone https://github.com/hans-brgs/realtime-calib && cd realtime-calib
cp .env.example .env          # fill in HOST_IP and the LiveKit keys

# Single stack (Caddy, plain HTTP, always on)
# Tablet: http://<HOST_IP>  ·  same-machine: http://localhost
docker compose up --build
```

Then open the webapp (`http://<HOST_IP>` on the tablet, or `http://localhost`).

The export preview's **copy** button needs a secure context: it works at `http://localhost`, not from another device (download the files instead).

## Documentation

The user documentation (installation, step-by-step guides, output file reference, methodology) is on the [project site](https://realtime-calib.hans-brgs.dev/docs/intro), and [RUNBOOK.md](RUNBOOK.md) covers running and troubleshooting the stack.

The design documentation (ADRs, entity and feature specs, roadmap) lives in a separate repository. Development follows a **spec-first / plan-review-implement / systematic-ADR** workflow described in the `CLAUDE.md` files (root and per service).

## Transparency & acknowledgements

- Inspired by [Caliscope](https://github.com/mprib/caliscope), created by Mac Prible.
- I use Claude Code (Opus 5.5) to assist me in writing the code.
