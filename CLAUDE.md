# CLAUDE.md — realtime-calib

> Ce fichier est lu automatiquement par Claude Code à chaque session.
> Il décrit le projet, les conventions et la manière de travailler ensemble.

---

## 🎯 Vue d'ensemble

Application **locale** de calibration multi-caméras **temps-réel** : intrinsèque (focale, distorsion) + extrinsèque (position/orientation 6-DoF) d'un ensemble de caméras USB, avec feedback live et export de fichiers **compatibles Caliscope**.

Inspirée de [Caliscope](https://github.com/mprib/caliscope) (logique de calibration, réimplémentée — pas une dépendance) et de l'écosystème vision-services Inmersiv (architecture temps-réel : LiveKit, webapp React/R3F, multiprocessing façon samvision).

Un opérateur lance le projet (Docker / `uv`), ouvre la webapp (desktop, **tablette**, mobile), et déroule un wizard : config caméras → board(s) → calibration intrinsèque caméra par caméra → calibration extrinsèque → revue 3D → export.

## 🧱 Services dans ce repo

| Service | Rôle | Stack |
| --- | --- | --- |
| `calibration-service/` | Capture + détection board + burn-in + publication LiveKit + calcul (intrinsèque/extrinsèque/BA) + API HTTP + état session | Python 3.12, `uv`, multiprocessing, asyncio, OpenCV, scipy, livekit |
| `calibration-webapp/` | Wizard opérateur + vue 3D | React, TypeScript, Vite, Mantine, Redux Toolkit, R3F/drei, React Compiler, yarn |
| `livekit-token-server/` | Émission de tokens JWT LiveKit | Python (Flask) |
| `caddy/` | Reverse proxy + terminaison TLS + statique | Caddy v2 |

L'orchestration est dans `docker-compose.yml`, qui ajoute aussi `livekit` (SFU WebRTC, image upstream `livekit/livekit-server`). **Stack unique** : Caddy (TLS) est le point d'entrée **obligatoire et toujours présent** ; l'accès same-machine se fait via `https://localhost`, l'accès tablette via `https://<HOST_IP>` (un seul certificat mkcert couvre les deux). Cf. ADR-0014 (supersede ADR-0006).

## 📃 Où est la documentation ?

La documentation vit dans un **repo séparé** : `realtime-calib-doc/`, ouvert comme vault Obsidian. Structure :

```
realtime-calib-doc/
├── 10-adr/             # Architecture Decision Records (immutables)
├── 20-specs/
│   ├── entities/       # Contrats de données (Camera, CalibrationBoard, CameraArrayConfig…)
│   └── features/       # Comportements end-to-end (flow intrinsèque, extrinsèque…)
├── 30-project/         # Roadmap, onboarding, glossaire, architecture overview
└── 100-templates/      # Templates ADR / entity / feature
```

Avant toute modification structurante (interface partagée, format de message inter-services, choix d'architecture), **consulter les ADRs et specs pertinents**.

## ⚡ Commandes essentielles

```bash
# Démarrage complet — stack unique (Caddy + TLS, toujours présent)
# Accès tablette : https://<HOST_IP>  ·  same-machine : https://localhost
docker compose up --build

# Redémarrage / logs d'un service
docker compose restart calibration-service
docker compose logs -f calibration-service

# Reconstruction après changement de Dockerfile/deps
docker compose build calibration-service && docker compose up -d calibration-service
```

Pas de Makefile ni de justfile — les commandes `docker compose` sont la source de vérité. Les commandes propres à chaque service sont dans `<service>/CLAUDE.md`.

## 📝 Workflow Git

- **Branches** : `feature/nom-de-la-feature` à partir de `main` pour toute feature ou correction non triviale.
- **PRs** sauf modifications mineures (typos, doc) qui peuvent aller directement sur `main`.
- **Conventional Commits** : `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `perf:`, `ci:`. Préfixer le scope : `feat(calibration-service): add charuco keyframe selection`.
- **Référencer les ADRs** quand un changement applique une décision : `Implements ADR-0008`.

## 🔐 Sécurité et secrets

- **Tous les secrets dans `.env`** (gitignored). Ne JAMAIS committer `.env`, `.env.*`, ni exposer une valeur sensible (clés LiveKit, etc.) dans le code.
- Toute valeur qui ressemble à un secret vient d'une variable d'environnement, pas d'une constante en dur.
- Certificats TLS dans `caddy/certs/` (gitignored).

## 🌐 Conventions cross-services

- **Réseau** : services via le réseau Docker `calib-network`. Internes (`calibration-service`, `livekit-token-server`) non exposés sur l'hôte — uniquement via `caddy`.
- **IP hôte** : centralisée dans `HOST_IP` (`.env`), propagée à `caddy`, `livekit`, et au build webapp (`VITE_HOST_IP`). Ne pas hardcoder d'IP ailleurs.
- **Pas de chemins absolus hardcodés** type `/home/hans/...` ou `C:/Users/...`. Tout chemin local via variable d'environnement.
- **Format de config compatible Caliscope** (ADR-0002) : ne pas casser la sémantique des champs natifs ; les champs propres sont additifs.
- **Langue** : les `README.md` (racine et par service) et **tous les commentaires dans le code** sont rédigés en **anglais** — par cohérence, les commentaires des fichiers de config committés (`.gitignore`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `Caddyfile`…) aussi. Le reste de la documentation (specs, ADRs, `CLAUDE.md`, journal d'ingénierie) reste en **français**.
- **LiveKit** : avant tout développement temps-réel touchant LiveKit (publication de tracks, data channel, tokens JWT, configuration réseau/ICE), consulter **https://docs.livekit.io/llms-full.txt** (doc LiveKit optimisée pour LLM) pour les bonnes pratiques et l'API à jour.
- **Pas de commentaires de signature ASCII** dans les nouveaux fichiers.
- **Markdown — RÈGLE ABSOLUE : jamais de retour chariot en milieu de phrase.** Dans **tout** fichier `.md` produit ou modifié (ADRs, specs, `README.md`, `CLAUDE.md`, journal d'ingénierie, doc du vault `realtime-calib-doc/`), un paragraphe s'écrit sur **une seule ligne logique** et c'est l'éditeur qui gère le retour à la ligne visuel. **Ne jamais hard-wrapper** à 72 / 80 / 100 colonnes, même si des fichiers existants alentour le font — ne pas imiter ce style. Les seuls sauts de ligne autorisés séparent des blocs : paragraphes, items de liste, titres, blocs de code, lignes de tableau.

---

## 🧭 Workflow agentic avec Claude Code

### Spec-first

Avant toute feature non triviale (nouveau flow, nouveau format de message inter-services, nouveau service), **rédiger une spec** dans `realtime-calib-doc/20-specs/` : objectif, contrat d'entrée/sortie, contraintes, tests clés. La spec est revue avant implémentation.

Pour les **entités** (structures partagées entre services, interfaces, modèles de config), spec obligatoire dans `20-specs/entities/` avant tout code. Templates dans `100-templates/`. Plusieurs specs sont déjà fournies ; d'autres sont **à écrire** (cf. `roadmap.md`).

### Plan-review-implement

Pour toute tâche > 30 min **OU** tout changement touchant le temps-réel (multiprocessing, sync inter-caméras, sérialisation, publication LiveKit, bundle adjustment) :

1. Proposer un **plan** étape par étape (pas de code).
2. L'utilisateur challenge / corrige.
3. Implémenter étape par étape.
4. Chaque étape se termine par un test qui passe **ou** une vérification manuelle explicite.
5. **Validation fichier par fichier** : après chaque fichier, expliquer l'implémentation et attendre l'aval avant de poursuivre.

Pour les modifications mineures (typo, log, constante), implémentation directe sans plan.

### ADR systématiques

Une décision architecturale (bibliothèque, pattern, trade-off de perf, format de protocole) = un ADR dans `realtime-calib-doc/10-adr/`. Format : Contexte → Facteurs → Options → Décision → Conséquences. Numérotés séquentiellement, **jamais supprimés** — superseded par un nouvel ADR. Si une modification applique une décision non couverte, **suggérer un ADR plutôt que procéder en silence**.

### Ce que Claude Code peut faire sans demander

- Lire n'importe quel fichier du repo et de `realtime-calib-doc/`.
- Lancer les commandes listées ici et dans les `<service>/CLAUDE.md`.
- Proposer des refactorings (en expliquant le pourquoi).
- Rédiger un brouillon d'ADR ou de spec à partir des templates.

### Ce qui nécessite confirmation explicite

- Installer de nouvelles dépendances (n'importe quel service).
- Modifier `docker-compose.yml` ou un `Dockerfile`.
- Mettre en place / modifier une CI.
- Toucher à `.env`, `.env.example`, ou gérer des secrets / certs.
- Supprimer ou renommer des fichiers existants.
- Modifier ce `CLAUDE.md` ou un `<service>/CLAUDE.md`.
- Modifier un ADR `accepted` (créer un superseded à la place).
- Modifier une spec déjà validée (`status: reviewed`/`implemented`).
- Modifier la configuration Caddy (`caddy/Caddyfile`).
- Pousser directement sur `main` (sauf modifs mineures).

---

## 🧪 Référence Caliscope

Caliscope (BSD-2-Clause) est la **référence conceptuelle** pour la logique de calibration. Points d'ancrage validés (**sur les sources**, main + v0.5.4, 2026-07-12) :
- Intrinsèque : Caliscope appelle `cv2.calibrateCamera` **sans aucun flag de modèle** (ADR-0032) → 5 coefficients `[k1, k2, p1, p2, k3]`, aspect libre. Notre implémentation : `cv2.calibrateCameraExtended` + `CALIB_USE_INTRINSIC_GUESS` seul (le variant Extended expose `perViewErrors` pour l'outlier-rejection, ADR-0009).
- Extrinsèque : PnP/`stereoCalibrate` pairwise, chaînage transitif depuis une ancre, bundle adjustment `scipy.least_squares` sur la capture volume.
- OpenCV mainline ≥ 4.7 (ArUco/ChArUco intégrés), API `CharucoDetector` (≥ 4.8).
- Format de sortie : TOML par caméra (`port`, `size`, `matrix`, `distortions`, `rotation` Rodrigues, `translation` en **mètres**, `error`, `grid_count` = nombre de **vues**). Cf. ADR-0002 et spec `camera-array-config`.

**Toujours grounder les claims sur la doc/le code Caliscope plutôt que sur des suppositions.**

## 🪜 Sub-CLAUDE.md

- `calibration-service/CLAUDE.md` — Python, multiprocessing, temps-réel, OpenCV/scipy, calibration.
- `calibration-webapp/CLAUDE.md` — React + Compiler, R3F/drei, Redux Toolkit, wizard FSM, responsive/tactile.
