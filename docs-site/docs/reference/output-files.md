---
sidebar_position: 4
---

# Output files

What realtime-calib writes when you export a calibration.

:::note Work in progress
Scaffold — to be expanded with exact file names, layout and an aniposelib sample.
:::

## Caliscope-compatible TOML

A per-camera TOML file holding the native Caliscope fields (`port`, `size`,
`matrix`, `distortions`, `rotation`, `translation`, `error`, `grid_count`). See
the [Configuration format](/docs/reference/configuration-format).

## aniposelib export

An [aniposelib](https://github.com/lambdaloop/aniposelib)-compatible export for
downstream triangulation / reconstruction pipelines.

## Session folder

Calibration artifacts are organized in a session folder that acts as the source of
truth for a given calibration run.
