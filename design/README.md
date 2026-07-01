---
created: 06-30-2026, 12:26:43 am
updated: 06-30-2026, 4:11:12 pm
---

# realtime-calib-doc

Documentation centralisée de **realtime-calib** — application locale de calibration multi-caméras **temps-réel** (intrinsèque + extrinsèque), inspirée de [Caliscope](https://github.com/mprib/caliscope) pour la logique de calibration et de l'écosystème **vision-services** (Inmersiv) pour l'architecture temps-réel (LiveKit, webapp React/R3F).

Ce repo est la **source de vérité partagée** pour les décisions architecturales, les spécifications techniques et la documentation projet. Il est volontairement séparé du repo de code `realtime-calib/` afin que son cycle de vie git reste indépendant et ne soit pas pollué par les modifications de code.

## En une phrase

Un opérateur lance le projet (Docker / `uv`), ouvre la webapp (desktop, **tablette** ou mobile), et déroule un wizard : configuration des caméras → calibration **intrinsèque** caméra par caméra → calibration **extrinsèque** multi-caméras → revue 3D et export de fichiers de config **compatibles Caliscope**. Tout se passe en temps-réel : flux caméra via LiveKit, jauges et overlays de qualité via data channel.

## Pour qui, pour quoi

| Audience | Usage |
| --- | --- |
| Développeur·euse(s) du projet | Source de vérité pour les décisions, contrats d'interface, comportements attendus |
| Onboarding nouveau collaborateur | Comprendre l'architecture sans lire tout le code |
| Claude Code et autres agents IA | Contexte structuré pour proposer des modifications cohérentes (cf. `CLAUDE.md` du repo de code) |

## Comment ouvrir la doc

### Comme vault Obsidian (recommandé)

1. Cloner le repo : `git clone <url> realtime-calib-doc`
2. Dans Obsidian : **Open folder as vault** → sélectionner le dossier cloné
3. Installer les plugins recommandés (Obsidian le propose au premier clone via `community-plugins.json`)

### Comme repo git classique

Tous les fichiers sont du markdown standard — lisible depuis GitHub, VS Code ou n'importe quel éditeur.

## Structure du repo

```
realtime-calib-doc/
├── 10-adr/             # Décisions architecturales (immutables, datées)
├── 20-specs/
│   ├── entities/       # Contrats de données (Camera, CalibrationBoard, CameraArrayConfig…)
│   └── features/       # Comportements end-to-end (flow intrinsèque, extrinsèque…)
├── 30-project/         # Roadmap, onboarding, glossaire, architecture overview
├── 100-templates/      # Templates de documents (ADR, entity, feature)
├── .obsidian/          # Config Obsidian locale (partiellement gitignorée)
├── .gitignore
└── README.md
```

### Pourquoi des préfixes numériques ?

Convention inspirée de [Johnny Decimal](https://johnnydecimal.com/) : tri stable dans l'explorateur (décisions d'abord, puis specs, puis project), saut de 10 entre catégories pour laisser de l'espace, templates en `100-` car ce sont une ressource de service, pas du contenu.

### Pourquoi pas une organisation par service ?

Les ADRs et features sont par nature **cross-services** (ex. le transport LiveKit entre `calibration-service` et `calibration-webapp`). L'organisation par type évite la duplication. Pour filtrer par service, utiliser les tags Obsidian : `#service/calibration-service`, `#service/calibration-webapp`.

## Les trois types de documents

### `10-adr/` — Architecture Decision Records

« Voici **pourquoi** on a choisi X plutôt que Y/Z à un moment donné. »

- **Immutable** : on ne modifie jamais un ADR `accepted`. On le supersede avec un nouveau.
- **Daté et numéroté** : `0001-reimplementation-coeur-calibration.md`, etc.
- **Court** : 1-2 pages.

### `20-specs/` — Spécifications techniques

**Entities** — « Voici **ce qu'est** une chose dans notre système. » Vivante, un concept = un fichier (`camera.md`, `calibration-board.md`, `camera-array-config.md`…).

**Features** — « Voici **comment fonctionne** un comportement de bout en bout. » Cross-entity, avec diagrammes de séquence et cas limites (`intrinsic-calibration-flow.md`…).

### `30-project/` — Documentation projet

Ni décision, ni spec : roadmap, onboarding, glossaire, vue d'ensemble architecturale.

## Où ranger un nouveau document

```
POURQUOI on a fait un choix ?              → 10-adr/
CE QU'EST une brique (data, concept) ?     → 20-specs/entities/
COMMENT des briques collaborent ?          → 20-specs/features/
Roadmap, onboarding, glossaire ?           → 30-project/
```

## Conventions

- **Nommage** : `kebab-case` uniquement, pas d'accents ni d'espaces. ADRs préfixés par leur ID à 4 chiffres.
- **Front-matter YAML** sur tout document structuré (cf. templates dans `100-templates/`).
- **Status** — ADR : `proposed` / `accepted` / `superseded` / `deprecated` · Entity : `stable` / `evolving` / `deprecated` · Feature : `planned` / `in-progress` / `implemented` / `deprecated`.
- **Tags** : `#service/*`, `#domain/calibration|capture|transport|board|infra|ui`, `#adr` / `#entity` / `#feature`.
- **Liens internes** : syntaxe Obsidian `[[nom-du-fichier]]`.
- **Diagrammes** : Mermaid (rendu natif GitHub + Obsidian).
- **Liens vers le code** : pointer un commit/tag précis de `realtime-calib/`, jamais `main`.
- **Langue** : français (langue de travail). Anglais pour les termes techniques sans bonne traduction (« keypoint », « reprojection error », « bundle adjustment », « board ») et les identifiants/tags.

## Lien avec Caliscope

Ce projet **réimplémente** la logique de calibration de Caliscope (cf. [[0001-reimplementation-coeur-calibration]]) pour la rendre temps-réel et incrémentale, mais reste **compatible au niveau du format de sortie** (cf. [[0002-format-sortie-compatible-caliscope]]). Caliscope reste la référence conceptuelle pour : la définition des boards (ChArUco/ArUco/chessboard), l'usage de `calibrateCameraCharucoExtended`, l'initialisation extrinsèque par PnP/stereo pairwise chaînés, et le bundle adjustment. Voir le [repo Caliscope](https://github.com/mprib/caliscope) (BSD-2-Clause).
