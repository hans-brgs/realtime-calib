---
sidebar_position: 1
---

# Development setup

Thanks for considering a contribution to realtime-calib!

:::note Work in progress
Scaffold — to be expanded with per-service dev loops and testing instructions.
:::

## Getting the code

```bash
git clone https://github.com/hans-brgs/realtime-calib
cd realtime-calib
```

## Running the stack

See [Installation](/docs/getting-started/installation). In short:

```bash
docker compose up --build          # default (Caddy + TLS)
docker compose --profile local up --build   # same machine
```

Service-specific commands (Python `uv`, web app `yarn`) live in each service's
`CLAUDE.md`.

## Before you open a PR

- Branch from `main`: `feature/<name>`.
- Follow **Conventional Commits** (`feat:`, `fix:`, `docs:`…), scoped where
  useful, e.g. `feat(calibration-service): …`.
- You will be asked to agree to the [Contributor License Agreement](/docs/contributing/cla).

:::info Documentation changes
This site lives in `docs-site/` in the main repo — update the docs in the **same
PR** as the behavior they describe.
:::
