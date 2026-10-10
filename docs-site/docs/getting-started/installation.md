---
sidebar_position: 1
description: "Install and run realtime-calib on your own hardware with Docker Compose — works on headless Linux servers; the web app is served over your local network."
keywords: [install camera calibration software, Docker camera calibration, headless Linux server, multi-camera rig]
---

# Installation

realtime-calib runs entirely on your own hardware. The complete application is run with **Docker Compose**: that is the one supported way to bring up the full stack.

## Prerequisites

- A **Linux host** with one or more **USB cameras** (read through V4L2).
- **Docker** and **Docker Compose v2**.
- A modern browser on the operator device (desktop, tablet or phone).

## One-time setup

```bash
# 1. Environment file
cp .env.example .env
# Edit .env: set HOST_IP to the machine's LAN IP (e.g. 192.168.1.42) and choose
# dedicated LiveKit API keys.
```

No certificate is needed: the stack serves plain HTTP on your local network.

:::info Host IP
The host IP is centralized in `HOST_IP` (in `.env`) and propagated to Caddy, LiveKit and the web app build. Do not hard-code IPs elsewhere.
:::

## Launch

The whole stack (calibration service, web app, LiveKit SFU, token server and the Caddy reverse proxy) is orchestrated by `docker-compose.yml` as a **single stack**. Caddy is the mandatory, always-on entry point.

```bash
docker compose up --build
```

Then open the web app:

- **Tablet, phone or other LAN device**: `http://<HOST_IP>`
- **Same machine**: `http://localhost`

The web app only receives video and data from the server, which browsers allow over plain HTTP. One small exception: the **copy** button of the export preview needs a secure context, so it works at `http://localhost` but not from a tablet; download the files instead.

## Verifying the install

```bash
docker compose logs -f calibration-service
```

Open `http://<HOST_IP>` (or `http://localhost`) and you should land on the operator **Dashboard** ("Welcome to the calibration bench").

## Where your data lives

Every calibration is a **session folder** under `./sessions/` on the host (mounted into the service). It holds the recordings, the board and camera configuration, the results and the exports. Back up or copy that folder to keep a calibration; nothing is stored in the browser.

## Networking notes

- **Caddy is the only host-exposed entry point**, serving plain HTTP on `80` (override with `CADDY_HTTP_PORT`). Everything else sits on an internal Docker bridge.
- Internal services are not published on the host: `calibration-service` (`8000`) and `livekit-token-server` (`8080`) are reachable only through Caddy.
- **LiveKit** runs on the bridge (not host networking). Its media ports are published to the host and advertised at `HOST_IP`: **UDP `50000-50010`** (WebRTC media) and **TCP `7881`** (ICE-TCP fallback). Signaling (`7880`) stays internal; Caddy proxies it as `ws`.
- Traffic on your local network is not encrypted. The stack is meant for a trusted LAN, not for exposure to the internet.

## Upgrading from an HTTPS install

Earlier versions served the web app over HTTPS with a locally generated certificate. To upgrade:

- In `.env`, `CADDY_HTTPS_PORT` is no longer read. Set `CADDY_HTTP_PORT` only if port 80 is taken.
- Pull the new version and rebuild the whole stack: `git pull`, then `docker compose up -d --build`. Every image changes, and the web app bakes its URLs in at build time.
- Open `http://<HOST_IP>`. A browser that cached a redirect to `https` may need the address retyped with `http://`.

Sessions recorded with an earlier version open as they are: their files are migrated when loaded. Solves computed by an earlier version are not comparable with new ones, because the calibration pipeline has changed since; recompute them to compare.

:::tip Next
Continue to the [Quickstart](/docs/getting-started/quickstart) to run your first calibration.
:::
