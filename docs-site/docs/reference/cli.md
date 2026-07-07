---
sidebar_position: 5
---

# CLI & commands

The source of truth for running the stack is **Docker Compose**. There is no
Makefile or justfile.

:::note Work in progress
Scaffold — service-specific commands live in each service's `CLAUDE.md`; this page
will collect the operator-facing ones.
:::

## Stack

```bash
# Full stack — default profile (Caddy + TLS)
docker compose up --build

# Local profile — same machine, no TLS
docker compose --profile local up --build
```

## Per-service

```bash
# Restart / follow logs
docker compose restart calibration-service
docker compose logs -f calibration-service

# Rebuild after a Dockerfile/deps change
docker compose build calibration-service && docker compose up -d calibration-service
```
