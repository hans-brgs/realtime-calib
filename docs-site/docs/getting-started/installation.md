---
sidebar_position: 1
---

# Installation

realtime-calib runs entirely on your machine. There are two supported ways to run
it: **Docker Compose** (recommended) or a local **`uv`** setup for the Python
service during development.

## Prerequisites

- One or more **USB cameras**.
- **Docker** and **Docker Compose v2** (for the default path).
- A modern browser on the operator device (desktop, tablet or mobile).

## Option 1 — Docker Compose (recommended)

The whole stack (calibration service, web app, LiveKit SFU, token server and the
Caddy reverse proxy) is orchestrated by `docker-compose.yml`.

```bash
# Default profile — Caddy + TLS, reachable from a tablet on the LAN
docker compose up --build
```

There are **two profiles**:

- **`default`** — Caddy reverse proxy with TLS termination. Use this to reach the
  web app from a **tablet** or another device on the network.
- **`local`** — same-machine only, `ws://localhost`, no Caddy/TLS.

```bash
# Local profile — same machine, no TLS
docker compose --profile local up --build
```

:::info Host IP
The host IP is centralized in `HOST_IP` (in `.env`) and propagated to Caddy,
LiveKit and the web app build. Do not hard-code IPs elsewhere.
:::

## Option 2 — Local `uv` (service development)

For iterating on the Python `calibration-service`, see its own `CLAUDE.md` for the
`uv` commands. The web app uses `yarn`.

## Verifying the install

Once the stack is up:

```bash
docker compose logs -f calibration-service
```

Then open the web app (the URL is printed by Caddy / the compose output) and you
should land on the operator **Dashboard**.

:::tip Next
Continue to the [Quickstart](/docs/getting-started/quickstart) to run your first
calibration.
:::
