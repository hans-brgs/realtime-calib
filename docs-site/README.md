# docs-site

Public documentation site for **realtime-calib**, built with
[Docusaurus](https://docusaurus.io/). See ADR-0024 (tooling & structure) and
ADR-0025 (licensing) in the documentation vault.

The site lives inside the main repo so docs travel with the code they describe
(docs-as-code) and versions align with code releases.

## Local development

```bash
yarn install
yarn start        # dev server with hot reload at http://localhost:3000
```

## Build

```bash
yarn build        # static site into ./build
yarn serve        # serve the built site locally
```

## Structure

```
docs/
├── intro.md              Introduction
├── getting-started/      Install + quickstart (tutorial)
├── guides/               How-to for each wizard step
├── reference/            Config format, HTTP API, entities, outputs, CLI
├── research/             Methodology (cited), benchmarks, bibliography, citation
├── architecture/         Overview + public ADRs
└── contributing/         Dev setup, license, CLA
blog/                     Release announcements
src/                      Landing page + theme (design system: ADR-0017)
static/img/               Logo & assets
```

## Versioning

Cut a documentation version aligned with a code release:

```bash
yarn docusaurus docs:version 1.0
```

## Deployment

Deployed to GitHub Pages by `.github/workflows/deploy-docs.yml` on pushes to
`main` that touch `docs-site/**`.
