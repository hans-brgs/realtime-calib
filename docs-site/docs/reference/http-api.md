---
sidebar_position: 2
---

# HTTP API

The `calibration-service` exposes an HTTP API for session state and control.

:::note Work in progress
Scaffold — the endpoint reference will be generated from the service's route
definitions to stay in sync with the code.
:::

## Conventions

- Base URL is served through **Caddy** (internal services are not exposed on the
  host directly).
- Requests and responses are JSON unless noted.

## Endpoint groups (planned structure)

| Group | Purpose |
| --- | --- |
| Session | Create / read / reset a calibration session |
| Cameras | Discovery, resolution, live preview control |
| Board | Board configuration |
| Intrinsics | Start/stop capture, per-camera results |
| Extrinsics | Anchor selection, solve, bundle adjustment |
| Export | Generate Caliscope-compatible output |

:::caution Do not hard-code URLs
Client code reaches the API through the reverse proxy using the configured host,
never a hard-coded IP.
:::
