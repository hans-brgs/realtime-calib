# CLAUDE.md — calibration-service

> Ce fichier complète le `CLAUDE.md` racine. Il est chargé en plus quand Claude Code
> travaille dans `calibration-service/`. Les conventions cross-services et le workflow
> agentic (spec-first, plan-review-implement, ADR) sont définis à la racine et
> **s'appliquent ici intégralement** — ce fichier ne fait que les compléter avec les
> spécificités du service.

---

## 🎯 Vue d'ensemble du service

Service Python responsable de toute la calibration temps-réel : capture multi-caméras → détection de board (ChArUco/ArUco) → burn-in des overlays → publication LiveKit + push télémétrie (data channel) → calcul (intrinsèque / extrinsèque / bundle adjustment) → écriture du dossier de calibration. Expose une **API HTTP** pour les commandes/config et **détient l'état de la session** (cf. ADR-0011). Tourne dans un conteneur Docker, accès caméras USB via `/dev/video*` (préférer `/dev/v4l/by-path/` pour la stabilité).

**CPU-only** (ADR-0013) : pas de GPU, pas de CUDA. La charge lourde est l'algèbre (`numpy`/`scipy`) et la détection OpenCV.

## 🏛️ Architecture interne

Service **unique** avec isolation **par process** (ADR-0005), orchestré par un lifecycle manager `asyncio` :

```
HTTP API (commandes/config)  ──┐
                               ├─►  Orchestrateur asyncio (lifecycle, EventBus, état session)
CaptureProcess ×N  ───────────►│         │
  (capture + détection board    │         ├─► observations (multiprocessing.Queue) ─► ComputeProcess
   + fill/sharpness + burn-in)   │         │                                            (intrinsèque,
                                 │         │                                             stereo pairwise,
  frames burn-in ──► LiveKitPublisher ◄───┘                                             bundle adjustment)
  agrégats ──────► data channel                                                ComputeProcess ─► dossier calib (disque)
```

- **CaptureProcess** (un par caméra, façon `PoseProcessor` samvision) : boucle de capture, détection de board, calcul `fill_fraction` + `sharpness`, sélection de keyframes (diversité + netteté, ADR-0008), burn-in des overlays à la résolution de preview (ADR-0003), émission des **observations** (coins + métadonnées, petites — ADR-0005) vers la queue de calcul.
- **ComputeProcess** (worker dédié) : reçoit les observations, exécute `calibrateCameraCharucoExtended` (+ stratégie d'optimisation, ADR-0009), l'init extrinsèque pairwise + chaînage transitif depuis l'ancre (ADR-0012), le **bundle adjustment** `scipy.optimize.least_squares`. Bloquant mais **isolé** → ne gèle ni la capture ni la preview. Écrit `camera_array.toml` (+ aniposelib).
- **FrameSynchronizer** (façon samvision) : pour l'extrinsèque, regroupe les observations multi-caméras par timestamp dans une fenêtre `< 1/fps`, quorum ≥ 2 (ADR-0007).
- **Orchestration asyncio** : API HTTP, publication LiveKit, data channel, lifecycle des process, EventBus interne, persistance de l'état session.

**Frontière async/multiprocessing** : `asyncio` pour l'orchestration et les I/O (HTTP, LiveKit, lifecycle) ; `multiprocessing` pour la capture/détection par caméra et le calcul (CPU-bound). Communication via `multiprocessing.Queue` côté process, events asyncio côté orchestration.

Modules (cible) sous `src/` :

- **`capture/`** : capture OpenCV, énumération/détection caméras (V4L2/UVC), `camera_health` (reconnexion backoff).
- **`detection/`** : détection ChArUco/ArUco (`cv2.aruco.CharucoDetector`, OpenCV ≥ 4.8), interpolation des coins, `fill_fraction`, `sharpness` (variance Laplacien).
- **`keyframes/`** : sélection par diversité de pose + rejet de flou (ADR-0008).
- **`overlays/`** : burn-in (polygone coloré selon `fill_fraction`, coins, IDs).
- **`calibration/`** : intrinsèque (`calibrateCameraCharucoExtended`), stratégies d'optimisation, extrinsèque (PnP/`stereoCalibrate` pairwise, chaînage transitif), `bundle_adjustment` (scipy), graphe de co-visibilité.
- **`synchronization/`** : `FrameSynchronizer` (timestamp, quorum).
- **`board/`** : définition + génération PNG à l'échelle physique.
- **`config/`** : lecture/écriture TOML (`rtoml`), schéma `camera_array.toml` (compat Caliscope, ADR-0002).
- **`session/`** : machine à états du wizard + persistance disque (source de vérité, ADR-0011).
- **`transport/`** : `LiveKitPublisher`, data channel, serveur HTTP (FastAPI ou équivalent).
- **`events/`** (EventBus) · **`models/`** (dataclasses / Pydantic).

## 🧱 Stack et tooling

- Python 3.12 (verrouillé par le Dockerfile et `pyproject.toml`).
- `uv` pour les dépendances (`pyproject.toml` + `uv.lock`).
- `multiprocessing` (capture par caméra + calcul, CPU-bound) ; `asyncio` (orchestration, I/O).
- Dépendances clés : `opencv-python` (mainline ≥ 4.8 pour `CharucoDetector` ; ArUco/ChArUco intégrés depuis 4.7), `numpy`, `scipy` (bundle adjustment `least_squares`), `rtoml` (config TOML façon Caliscope), `livekit`/`livekit-api` (publication + data channel), serveur HTTP (`fastapi` + `uvicorn` recommandé). Envisager `numba` si le BA devient un goulot (comme Caliscope). **Liste exacte : `pyproject.toml`.**
- Outillage qualité : `ruff` (lint + format), `mypy --strict`, `pytest` (+ `pytest-benchmark`, `hypothesis` si utile).

## ⚡ Commandes spécifiques au service

```bash
# Installer les dépendances (depuis calibration-service/)
uv sync

# Lancement local hors Docker (depuis calibration-service/)
uv run python -m calibration_service.app

# Lancement via Docker (depuis la racine du repo)
docker compose up calibration-service

# Logs
docker compose logs -f calibration-service

# Rebuild après changement de Dockerfile ou de dépendances
docker compose build calibration-service && docker compose up -d calibration-service
```

Outillage qualité (`uv`, depuis `calibration-service/`) :

```bash
uv run pytest                 # tests
uv run ruff check             # lint
uv run ruff format            # format
uv run mypy src/              # type check
```

## 📝 Conventions Python

### Typing

- `from __future__ import annotations` en haut de chaque fichier.
- Types explicites sur toutes les signatures publiques (paramètres et retour).
- `list[T]` plutôt que `List[T]`, `T | None` plutôt que `Optional[T]`.
- Pas de `Any` sans commentaire justifiant.

### Nommage

- **Modules & fonctions** : `snake_case` · **Classes & Pydantic models** : `PascalCase` · **Constantes** : `SCREAMING_SNAKE_CASE`.
- **Tests** : `test_<ce_qui_est_testé>.py`, fonctions `test_<comportement_attendu>`.

### Structure d'un fichier Python

1. `from __future__ import annotations` 2. stdlib 3. tiers 4. locaux (absolus) 5. constantes 6. types & protocols 7. fonctions/classes.

### Pydantic / dataclasses

- `ConfigDict(frozen=True)` (ou dataclasses `frozen=True`) sur les modèles partagés entre process — évite les mutations accidentelles. Les porteurs d'observations échangés via queue sont **petits et immuables**.
- Un modèle par fichier quand c'est structurant.

### Docstrings

- Sur les méthodes publiques et fonctions non triviales. Format libre lisible.

## ⏱️ Patterns à respecter (real-time + multiprocessing)

- **Méthodes statiques pour les targets multiprocessing** : la fonction passée à `Process(target=...)` doit être une `@staticmethod` ou module-level (sérialisation). Modèle : la boucle de capture.
- **Cleanup explicite des Queues dans `finally`** : vider la queue de sortie à l'arrêt d'un process pour éviter fuites de descripteurs et zombies.
- **Drop frame plutôt que blocking** : queue de sortie pleine → retirer l'ancienne frame avant d'ajouter la nouvelle. Le temps-réel préfère perdre une frame qu'attendre. (Vaut pour la preview ; les **observations** retenues, elles, ne se droppent pas en silence — elles sont la donnée de calibration.)
- **Try/except autour des appels caméra** : une caméra USB peut disparaître ; dégrader gracieusement (skip, log, retry) plutôt que crasher.
- **`copy()` explicite avant modification numpy/OpenCV** sur une frame partagée (les vues numpy partagent la mémoire). Particulièrement avant le burn-in (on dessine sur une copie destinée à la preview, pas sur la frame servant à la détection).
- **Détection à la résolution de calibration, burn-in à la résolution de preview** : scaler les coins détectés (calibration → preview) avant de dessiner (ADR-0003).
- **Timestamp host monotone à la capture** : c'est la seule base de synchro extrinsèque (jamais le numéro de frame, non comparable entre caméras) — ADR-0007.
- **Bundle adjustment dans le ComputeProcess uniquement** : jamais dans la boucle de capture ni l'event loop asyncio.

## 🚫 Patterns à éviter

- **`print()` dans le code** — utiliser `logging` (`logger = logging.getLogger(__name__)`).
- **`time.sleep()` dans une boucle de pipeline** — `asyncio.sleep()` côté async ou `multiprocessing.Event.wait(timeout=...)` côté process.
- **`pickle` pour le wire format** (HTTP/data channel) — JSON ; `multiprocessing.Queue` gère le pickle inter-process nativement.
- **`except: pass`** sans logger — au minimum `logger.exception(...)`.
- **Modifier des attributs partagés sans synchronisation** entre process — `multiprocessing.Event`/`Value`/`Array`/`Manager` selon le besoin.
- **Hardcode de chemins** (`/home/hans/...`, `C:/Users/...`) — variables d'environnement (`CALIB_*`).
- **Globals mutables au niveau module** — préférer constante, injection, ou `functools.cache`.
- **Minimiser le RMSE en supprimant des frames naïvement** — anti-pattern explicite (ADR-0009) : la couverture prime sur le RMSE.
- **Casser la sémantique des champs Caliscope** dans le TOML de sortie — les champs natifs gardent leur sens, les extensions sont additives (ADR-0002).

## 🧪 Tests

`pytest` (config dans `pyproject.toml`). Cibler en priorité :

- **Non-régression numérique vs Caliscope** sur un dataset de référence (intrinsèque : RMSE comparable ; extrinsèque : positions caméras à ± tolérance). C'est le garde-fou central de la réimplémentation (ADR-0001).
- Sélection de keyframes (diversité + rejet de flou).
- Synchronisation par timestamp (fenêtre, quorum).
- Stratégie `coverage-aware` : ne descend jamais sous le plancher de couverture.
- Lecture/écriture du `camera_array.toml` (round-trip, compat Caliscope).

Pour une zone non couverte, le dire explicitement ("pas de test — vérification manuelle requise") plutôt que de prétendre l'avoir testée.

## 🔍 Profiling

`py-spy` (sampling, prod-safe), `cProfile` (dev). **Mesurer avant d'optimiser.** Le bundle adjustment et les recalibrations (`coverage-aware`) sont les suspects CPU ; `numba` est une piste (Caliscope l'utilise) avant toute réécriture.

## ⚙️ Configuration et démarrage

Configuré via variables d'environnement (`.env` racine, propagé par `docker-compose.yml`). **Source de vérité : la classe `Config` du service.** Grandes familles (cible) :

- **HTTP** : `CALIB_HTTP_HOST`, `CALIB_HTTP_PORT`.
- **Caméras** : énumération dynamique (pas de liste statique façon samvision) ; `CALIB_CAMERA_BACKEND`, contraintes de format/fps par défaut.
- **Dossier de calibration** : `CALIB_SESSIONS_DIR` (racine des dossiers de session, source de vérité — ADR-0011).
- **LiveKit** : `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_ROOM_NAME`.
- **Déploiement** : profil `default` (Caddy+TLS) vs `local` (ADR-0006).

Point d'entrée : `app.py` — long-running, piloté par l'API HTTP (start/stop capture, compute, etc.).

## 📚 Références doc

Quand un changement structurant est demandé, consulter `realtime-calib-doc/` :

- `10-adr/` — décisions (réimplémentation 0001, format 0002, burn-in 0003, transport 0004, service unique 0005, sync 0007, keyframes 0008, optimisation 0009, source de vérité 0011, ancre 0012, CPU-only 0013).
- `20-specs/entities/` — `camera`, `calibration-board`, `camera-array-config`, `calibration-session`, `board-observation`, `coverage-metrics`.
- `20-specs/features/` — `intrinsic-calibration-flow`, `extrinsic-calibration-flow` (+ specs à écrire, cf. `roadmap.md`).

## 🧪 Ancrage Caliscope (rappel)

Grounder sur le code/la doc Caliscope, pas sur des suppositions :
- Intrinsèque : `cv2.aruco.calibrateCameraCharucoExtended`, flags `CALIB_USE_INTRINSIC_GUESS + CALIB_RATIONAL_MODEL + CALIB_FIX_ASPECT_RATIO`, `perViewErrors` exposés.
- Distorsion : modèle 5 coefficients `[k1, k2, p1, p2, k3]`.
- Rotation stockée en Rodrigues 3-vecteur par caméra ; `cv2.Rodrigues` pour passer en 3×3.
- Extrinsèque : pairwise PnP/`stereoCalibrate`, chaînage transitif, bundle adjustment scipy sur la capture volume {caméras + points 3D}.
