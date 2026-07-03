---
sidebar_position: 2
---

# Decision records (ADRs)

Architectural decisions are captured as **ADRs**. The full set lives in the
project's internal documentation vault; a **curated public subset** is surfaced
here for transparency.

:::note Work in progress
Scaffold — the curated list of published ADRs will be linked here. Not every
internal ADR is published; the public subset focuses on decisions relevant to
users, integrators and contributors.
:::

## Format

Each ADR follows: **Context → Decision factors → Options → Decision →
Consequences**. ADRs are numbered sequentially and never deleted — a decision that
changes is *superseded* by a new ADR.

## Selected public decisions

- Reimplementing the calibration core (vs. depending on Caliscope).
- Caliscope-compatible output format.
- Real-time transport over LiveKit.
- Single Python calibration service.
- Documentation site: this site (Docusaurus).
- Licensing: AGPL-3.0 + commercial license + CLA.

→ Licensing details: [License](/docs/contributing/license).
