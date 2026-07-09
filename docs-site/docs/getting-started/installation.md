---
sidebar_position: 1
---

# Installation

realtime-calib runs entirely on your own hardware. The complete application is
run with **Docker Compose** — that is the one supported way to bring up the full
stack.

## Prerequisites

- One or more **USB cameras**.
- **Docker** and **Docker Compose v2**.
- **mkcert** and **openssl** — to issue the local TLS certificate the stack
  needs. WebRTC requires a secure context, and LAN devices (e.g. a tablet) must
  trust the certificate.
- A modern browser on the operator device (desktop, tablet or mobile).

## One-time setup

```bash
# 1. Environment file
cp .env.example .env
# Edit .env: set HOST_IP to the machine's LAN IP (e.g. 192.168.1.42) and choose
# dedicated LiveKit API keys.

# 2. Trust the mkcert local CA (once per machine)
mkcert -install

# 3. Generate the LAN certificate (covers HOST_IP + localhost)
./caddy/generate-certs.sh
# -> writes caddy/certs/livekit.crt and caddy/certs/livekit.key (gitignored)
```

:::info Host IP
The host IP is centralized in `HOST_IP` (in `.env`) and propagated to Caddy,
LiveKit and the web app build. Do not hard-code IPs elsewhere.
:::

## Launch

The whole stack — calibration service, web app, LiveKit SFU, token server and the
Caddy reverse proxy (TLS) — is orchestrated by `docker-compose.yml` as a **single
stack**. Caddy is the mandatory, always-on entry point.

```bash
docker compose up --build
```

Then open the web app:

- **Tablet / other LAN device**: `https://<HOST_IP>`
- **Same machine**: `https://localhost`

One mkcert certificate covers both.

## Verifying the install

```bash
docker compose logs -f calibration-service
```

Open `https://<HOST_IP>` (or `https://localhost`) and you should land on the
operator **Dashboard** ("Welcome to the calibration bench").

## Networking notes

- **Caddy is the only host-exposed entry point**, serving HTTPS on `443` (override
  with `CADDY_HTTPS_PORT`). Everything else sits on an internal Docker bridge.
- Internal services are not published on the host: `calibration-service` (`8000`)
  and `livekit-token-server` (`8080`) are reachable only through Caddy.
- **LiveKit** runs on the bridge (not host networking). Its media ports are
  published to the host and advertised at `HOST_IP`: **UDP `50000-50010`** (WebRTC
  media) and **TCP `7881`** (ICE-TCP fallback). Signaling (`7880`) stays internal —
  Caddy proxies it as `wss`.
- TLS certificates live in `caddy/certs/` (git-ignored); one mkcert certificate
  covers `HOST_IP` and `localhost`.

:::tip Next
Continue to the [Quickstart](/docs/getting-started/quickstart) to run your first
calibration.
:::
