---
sidebar_position: 6
---

# Deployment

How to run realtime-calib for same-machine use versus tablet/LAN access.

:::note Work in progress
Scaffold page — to be expanded with TLS/cert setup and network topology diagrams.
:::

## Two Compose profiles

| Profile | Transport | Reverse proxy | Use it for |
| --- | --- | --- | --- |
| `default` | `wss://` (TLS) | Caddy | Tablet / another device on the LAN |
| `local` | `ws://localhost` | none | Same-machine operation |

```bash
# Tablet / LAN access
docker compose up --build

# Same machine only
docker compose --profile local up --build
```

## Networking notes

- Internal services (`calibration-service`, `livekit-token-server`) are **not**
  exposed on the host — only through **Caddy**.
- The host IP lives in `HOST_IP` (`.env`) and is propagated to Caddy, LiveKit and
  the web app build. Do not hard-code IPs.
- TLS certificates live in `caddy/certs/` (git-ignored).
