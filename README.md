# realtime-calib

A **local**, **real-time** multi-camera calibration application: intrinsics
(focal length, distortion) + extrinsics (6-DoF position/orientation) for a set of
USB cameras, with live feedback and export of **Caliscope-compatible** files.

An operator starts the project, opens the webapp (desktop, **tablet**, mobile)
and follows a wizard: camera config → board(s) → per-camera intrinsic
calibration → extrinsic calibration → 3D review → export.

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

## Documentation

Documentation (ADRs, entity and feature specs, roadmap) lives in the separate
`realtime-calib-doc/` repository (Obsidian vault). Development follows a
**spec-first / plan-review-implement / systematic-ADR** workflow described in the
`CLAUDE.md` files (root and per service).

## Development

<!-- TODO (Hans): document the use of Claude Code as the coding agent here. -->
