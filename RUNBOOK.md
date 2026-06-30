# Runbook — realtime-calib

Operational guide: TLS certificates, launching the stack, and troubleshooting.
Single stack with Caddy as the mandatory entry point (ADR-0014).

## 1. One-time setup

```bash
# Prerequisites: Docker, uv, mkcert, openssl, Node 24 + yarn 4 (corepack) for local front work.

# Environment file
cp .env.example .env
# Edit .env: set HOST_IP to the machine's LAN IP (e.g. 192.168.1.42) and choose
# dedicated LiveKit API keys.

# Trust the mkcert local CA (once per machine)
mkcert -install

# Generate the LAN certificate (covers HOST_IP + localhost), long-lived (~100 years).
# Signed by the mkcert root CA so browsers trust it; HOST_IP is read from .env.
# Override inline: HOST_IP=... CERT_DAYS=... ./caddy/generate-certs.sh
./caddy/generate-certs.sh
# -> writes caddy/certs/livekit.crt and caddy/certs/livekit.key (gitignored)
```

## 2. Launch

```bash
docker compose up --build
```

Then open:

- **Tablet / other LAN device**: `https://<HOST_IP>`
- **Same machine**: `https://localhost`

The calibration-service publishes one LiveKit video track per detected USB camera;
the webapp preview shows one tile per track.

## 3. Stopping / logs / rebuild

```bash
docker compose down                         # stop
docker compose logs -f calibration-service  # follow logs
docker compose build calibration-service    # rebuild one service after dep changes
```

## 4. Troubleshooting

### Cameras not detected (no tiles)
- Confirm the host sees them: `ls -l /dev/video*` and `ls /dev/v4l/by-path/`.
- The container needs device passthrough — already wired in `docker-compose.yml`
  (`/dev:/dev` + `device_cgroup_rules: "c 81:* rmw"`).
- Another process may hold the camera (e.g. another capture stack). Free it first.
- Check the logs: `docker compose logs calibration-service | grep -i camera`.

### Browser TLS warning
- The cert must cover the host you typed: regenerate with the right `HOST_IP`.
- Ensure `mkcert -install` ran on the **browsing** device's trust store (for a
  tablet, install the mkcert root CA on the tablet).

### LiveKit media not flowing (tiles stay black)
- LiveKit runs on the **host network** and binds `7880` (signal), `7882` (UDP
  media), `7883` (TCP media fallback) directly on the host. They must be free and
  reachable at `HOST_IP`. Override the UDP media port with `LIVEKIT_UDP_PORT` in
  `.env` if it clashes.
- `NODE_IP` (from `HOST_IP`) must be the LAN IP reachable by clients.
- Why host network: the in-container publisher (`calibration-service`) cannot
  hairpin to a *published* port (UDP or TCP) over the docker bridge; it can only
  reach LiveKit as a *real* host listener at `HOST_IP`. Same single endpoint then
  serves LAN browsers.

### Running alongside another LiveKit (e.g. samvision)
- This stack's LiveKit uses host ports `7880`/`7882`/`7883`, which do not overlap a
  typical second LiveKit (`7881` + `50000-50010`), so both can run at once.
- Only `443` (Caddy) may still clash — override `CADDY_HTTPS_PORT` in `.env`.

### Port 443 already in use
- Another service holds it. Override `CADDY_HTTPS_PORT` in `.env` (then access
  `https://<HOST_IP>:<port>`).
