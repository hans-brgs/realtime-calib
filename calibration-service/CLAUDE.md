# CLAUDE.md — calibration-service

> Ce fichier complète le `CLAUDE.md` racine. Il est chargé en plus quand Claude Code travaille dans `calibration-service/`. Les conventions cross-services et le workflow agentic (spec-first, plan-review-implement, ADR) sont définis à la racine et **s'appliquent ici intégralement** — ce fichier ne fait que les compléter avec les spécificités du service.

---

## 🎯 Vue d'ensemble du service

Service Python responsable de toute la calibration temps-réel : capture multi-caméras → détection de board (ChArUco/ArUco) → burn-in des overlays → publication LiveKit + push télémétrie (data channel) → calcul (intrinsèque / extrinsèque / bundle adjustment) → écriture du dossier de calibration. Expose une **API HTTP** pour les commandes/config et **détient l'état de la session** (cf. ADR-0011). Tourne dans un conteneur Docker, accès caméras USB via `/dev/video*` (préférer `/dev/v4l/by-path/` pour la stabilité).

**CPU-only** (ADR-0013) : pas de GPU, pas de CUDA. La charge lourde est l'algèbre (`numpy`/`scipy`) et la détection OpenCV.

## 🏛️ Architecture interne

Un **seul process**, avec le modèle de concurrence d'ADR-0050 (proposed ; il supersède à son acceptation l'isolation par process d'ADR-0005, jamais construite) :

```
Boucle asyncio : API HTTP (FastAPI), connexion LiveKit, cycle de vie et réconciliation des caméras (ADR-0021, ADR-0029)
  ├─ par caméra ouverte : une boucle de capture (asyncio) + un thread dédié au device (CameraDevice : open, read, grab, retrieve, choose_clock, release)
  ├─ pool partagé de threads (_CAPTURE_THREADS) : réduction, détection, burn-in, écritures vidéo — jamais d'appel au device
  └─ exécuteur par défaut (run_in_executor) : calculs intrinsèque / extrinsèque / Minimize / import ;
     une opération longue ou mutante à la fois pour tout le service (verrou, 409 busy), l'orientation comprise (elle tourne sur la boucle)
```

- **Capture** (`transport/camera_publish_service.py`) : une boucle par caméra garde la première frame de chaque cellule d'une grille absolue (ADR-0037), horodatée par le timestamp V4L2 de son buffer, ou l'horloge monotone de l'hôte à défaut (ADR-0049). Elle publie la preview sur LiveKit à sa propre cadence, `preview_fps` plafonné par celle de la capture et modifiable à chaud (ADR-0015, ADR-0036, ADR-0037). La caméra intrinsèque active — ou toutes les caméras en vue extrinsèque et pendant un sweep — détecte la board sur sa propre grille, à la résolution de preview (ADR-0031), pousse la télémétrie (data channel) et enregistre les vidéos natives pendant une capture intrinsèque ou un sweep extrinsèque.
- **Calculs** : ils relisent toujours les enregistrements natifs sur disque et y re-détectent la board (replay, ADR-0019, ADR-0022), jamais un flux live. L'intrinsèque dans `calibration/intrinsic.py` ; l'extrinsèque dans le paquet `calibration/extrinsic/`, orchestré par `session/workflow.py` (pairwise `stereoCalibrate` et chaînage depuis l'ancre ADR-0012, ADR-0023 ; bundle adjustment `scipy.optimize.least_squares` avec rigidité de la cible et passe robuste `soft_l1`, ADR-0044, ADR-0046).
- **Synchronisation extrinsèque** : le compute regroupe les frames des sidecars de timestamps dans une fenêtre `< 1/fps`, quorum ≥ 2 (ADR-0007) ; le `FrameSynchronizer` live ne sert qu'à la co-visibilité affichée pendant le sweep.
- **Règles de concurrence** (ADR-0050) : un `release` ne croise jamais un `grab` (file FIFO du thread du device) ; toute attente dont dépend un invariant passe par `run_to_completion` ; un démontage annule toutes ses tâches puis les attend toutes ; pas de `suppress(CancelledError)` autour de l'attente d'une autre tâche.

Modules sous `src/calibration_service/` :

- **`capture/`** : `CameraDevice` (un thread par caméra), capture OpenCV et timestamps (ADR-0049), énumération V4L2/UVC, `camera_health` (backoff de réouverture), pacing par grille.
- **`detection/`** : `BoardDetector` (ChArUco/ArUco, `CharucoDetector` OpenCV ≥ 4.8), raffinement des coins single-marker par la bordure (ADR-0052), netteté, couverture.
- **`overlays/`** : burn-in de la preview (ADR-0003).
- **`calibration/`** : `intrinsic.py` (sélection des keyframes ADR-0038, `calibrateCameraExtended` sans flag de modèle ADR-0032, deux initialisations ADR-0053), `uncertainty.py` (ADR-0055), `motion.py` (ADR-0056), et le paquet `extrinsic/` (`model`, `sweep`, `init`, `bundle`, `frame`, `pipeline`).
- **`synchronization/`** : fenêtre de synchro, `FrameSynchronizer`, graphe de co-visibilité, alignement des timestamps Caliscope.
- **`board/`** : dictionnaires, validation, le board OpenCV unique (`charuco_board`), rendu PNG.
- **`recording/`** : enregistrement vidéo et sidecars, previews mp4, ffmpeg, replay.
- **`session/`** : `SessionManager` (état, source de vérité ADR-0011), store TOML, boards (`config_store`), `workflow` (le solve extrinsèque partagé par l'API et les outils), `layout` (noms de fichiers), import d'une session préenregistrée (`import_contract`, `import_sidecars`, `import_session`, ADR-0035).
- **`export/`** : cibles d'export (Caliscope, aniposelib, plateformes, OpenCV ADR-0057), contrôles avant export, recalage sur une référence (ADR-0061).
- **`transport/`** : API HTTP (`api.py`), service de publication des caméras, `LiveKitPublisher`.
- **`models/`** (dataclasses) · `tuning.py` (défauts et bornes, ADR-0036) · `settings.py` (réglages du rig) · `site_template.py` (gabarit du site, ADR-0062).
- Modules de premier niveau : `app.py` (création de l'app et cycle de vie), `config.py` (`Config`, `LiveKitConfig`, variables d'environnement), `concurrency.py` (`run_to_completion`, `settle`, les outils des règles ci-dessus), `telemetry.py` (payloads du data channel), `atomic_io.py` (écritures atomiques, ADR-0011), `resolution.py` (native ↔ sortie, ADR-0015, ADR-0051), `logging_setup.py`.
- Aussi : `capture/source.py` (protocole `VideoSource`), `transport/frame_conversion.py` (frames vers LiveKit).
- **`tools/`** (hors paquet, lancés depuis l'hôte avec `uv`, absents de l'image Docker) : `eval_extrinsic_session.py` (rejeu d'une session), `probe_capture.py` (test de capture USB à la main). `mypy` ne les couvre pas par défaut : `MYPYPATH=src uv run mypy --strict tools/`.

## 🧱 Stack et tooling

- Python 3.14 (verrouillé par le Dockerfile — `python:3.14-slim-trixie` — et `pyproject.toml` — `requires-python = ">=3.14"`).
- `uv` pour les dépendances (`pyproject.toml` + `uv.lock`).
- `asyncio` (orchestration, I/O) et threads (un par caméra, un pool partagé, l'exécuteur par défaut pour les calculs) — ADR-0050.
- Dépendances clés : `opencv-python` (mainline ≥ 4.8 pour `CharucoDetector` ; ArUco/ChArUco intégrés depuis 4.7), `numpy`, `scipy` (bundle adjustment `least_squares`), `rtoml` (config TOML façon Caliscope), `livekit`/`livekit-api` (publication + data channel), serveur HTTP (`fastapi` + `uvicorn`). Envisager `numba` si le BA devient un goulot (comme Caliscope). **Liste exacte : `pyproject.toml`.**
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

- `ConfigDict(frozen=True)` (ou dataclasses `frozen=True`) sur les valeurs partagées entre threads (détections, résultats de calcul) — évite les mutations accidentelles. Exception : l'état de session (`CalibrationSession`, `CameraConfig`, `CalibrationBoard`) est mutable, modifié en place par le `SessionManager` seul, puis persisté.
- Un modèle par fichier quand c'est structurant.

### Docstrings

- Sur les méthodes publiques et fonctions non triviales. Format libre lisible.

## ⏱️ Patterns à respecter (real-time + threads, ADR-0050)

- **Tout appel à un device sur son thread** (`CameraDevice`) : jamais un `grab`, `retrieve` ou `release` depuis le pool partagé ou la boucle asyncio. Le `release` est idempotent et s'exécute après le `grab` en cours.
- **Rien de plus de 20 ms sur la boucle asyncio** : une ouverture de caméra, une énumération, un calcul passent dans un thread (`asyncio.to_thread`, `run_in_executor`). La sonde d'énumération, qui ouvre brièvement les devices que le service ne tient pas, passe par `asyncio.to_thread`, pas par le thread d'un device (ADR-0050 §3).
- **Une opération longue ou mutante à la fois** : les routes concernées prennent le verrou applicatif sans attendre, et répondent 409 `busy` s'il est pris ; pendant un enregistrement, ni la configuration des caméras ni un changement de session (409 `recording`).
- **Des attentes qui résistent à l'annulation** : `run_to_completion` pour un `release`, une écriture sous verrou, une ouverture ; un démontage annule tout puis attend tout.
- **Drop frame plutôt que blocking** : la grille de capture garde la première frame de chaque cellule et jette les autres sans les décoder (ADR-0037). Le temps-réel préfère perdre une frame qu'attendre ; un enregistrement, lui, écrit chaque frame gardée.
- **Try/except autour des appels caméra** : une caméra USB peut disparaître ; une frame dont le traitement échoue est sautée, une perte prolongée remonte à la réconciliation (#46, #48).
- **`copy()` explicite avant modification numpy/OpenCV** sur une frame partagée (les vues numpy partagent la mémoire). Particulièrement avant le burn-in (on dessine sur une copie destinée à la preview, pas sur la frame servant à la détection).
- **Détection live à la résolution de preview, re-détection native au calcul** : la boucle réduit l'image une fois, y détecte et y dessine les coins à l'échelle 1 (ADR-0031, ADR-0038) ; les calculs re-détectent les vidéos natives (ADR-0003 pour le burn-in).
- **Timestamp du buffer V4L2, ou l'horloge monotone de l'hôte à défaut** (une base choisie une fois à l'ouverture, ADR-0049) : seule base de la synchro extrinsèque, jamais le rang de frame (non comparable entre caméras) — ADR-0007.
- **Calculs hors de la boucle de capture et de la boucle asyncio** : intrinsèque, extrinsèque et bundle adjustment tournent dans l'exécuteur par défaut, sur les enregistrements.

## 🚫 Patterns à éviter

- **`print()` dans le code** — utiliser `logging` (`logger = logging.getLogger(__name__)`).
- **`time.sleep()` dans une boucle de pipeline** — `asyncio.sleep()` côté async ou `threading.Event.wait(timeout=...)` côté thread.
- **`pickle` pour le wire format** (HTTP/data channel) ou un fichier de session — JSON ou TOML.
- **`except: pass`** sans logger — au minimum `logger.exception(...)`.
- **Modifier des attributs partagés sans synchronisation** entre threads — un verrou (`asyncio.Lock` côté boucle, `threading.Lock` côté threads) selon le besoin.
- **Hardcode de chemins** (`/home/hans/...`, `C:/Users/...`) — variables d'environnement (`CALIB_*`).
- **Globals mutables au niveau module** — préférer constante, injection, ou `functools.cache`.
- **Minimiser le RMSE en supprimant des frames naïvement** — anti-pattern explicite (ADR-0059, qui reprend la règle d'ADR-0009) : la couverture prime sur le RMSE ; une évolution qui écarte des vues doit montrer un gain hors échantillon.
- **Casser la sémantique des champs Caliscope** dans le TOML de sortie — les champs natifs gardent leur sens, les extensions sont additives (ADR-0002).

## 🧪 Tests

`pytest` (config dans `pyproject.toml`). Cibler en priorité :

- **Non-régression numérique vs Caliscope** sur un dataset de référence (intrinsèque : RMSE comparable ; extrinsèque : positions caméras à ± tolérance). C'est le garde-fou central de la réimplémentation (ADR-0001).
- Sélection de keyframes (diversité, netteté relative dans la cellule, ADR-0038).
- Synchronisation par timestamp (fenêtre, quorum).
- Lecture/écriture du `camera_array.toml` (round-trip, compat Caliscope).

Un refactor du solveur se vérifie en plus à la main : identité des résultats d'un compute et d'un Minimize sur une session réelle (protocole manuel, aucun outil du repo ne rejoue Minimize).

Pour une zone non couverte, le dire explicitement ("pas de test — vérification manuelle requise") plutôt que de prétendre l'avoir testée.

## 🔍 Profiling

`py-spy` (sampling, prod-safe), `cProfile` (dev). **Mesurer avant d'optimiser.** La détection par image (live et parcours du sweep au compute) et le bundle adjustment sont les suspects CPU ; `numba` est une piste (Caliscope l'utilise) avant toute réécriture.

## ⚙️ Configuration et démarrage

Configuré via variables d'environnement (`.env` racine, propagé par `docker-compose.yml`). **Source de vérité : la classe `Config` du service.** Grandes familles (cible) :

- **HTTP** : `CALIB_HTTP_HOST`, `CALIB_HTTP_PORT`.
- **Caméras** : énumération dynamique (pas de liste statique façon samvision) ; le backend et les contraintes de format/fps relèvent des réglages et de `TUNING` (ADR-0036), pas de variables d'environnement.
- **Dossier de calibration** : `CALIB_SESSIONS_DIR` (racine des dossiers de session, source de vérité — ADR-0011).
- **LiveKit** : `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_ROOM_NAME`.
- **Déploiement** : stack unique, Caddy (TLS) point d'entrée obligatoire et toujours présent ; same-machine via `https://localhost` (ADR-0014, supersede ADR-0006).

Point d'entrée : `app.py` — long-running, piloté par l'API HTTP (start/stop capture, compute, etc.).

## 📚 Références doc

Quand un changement structurant est demandé, consulter `realtime-calib-doc/` :

- `10-adr/` — décisions (réimplémentation 0001, format 0002, burn-in 0003, transport 0004, sync 0007, keyframes 0008 et 0038, source de vérité 0011, ancre 0012, CPU-only 0013, solveur extrinsèque 0023, concurrence 0050 et un seul solve intrinsèque 0059 — tous deux proposed, qui supersèdent 0005 et 0009 à leur acceptation ; la liste complète est dans le vault).
- `20-specs/entities/` — `camera`, `calibration-board`, `camera-array-config`, `calibration-session`, `board-observation`, `coverage-metrics`, `livekit-transport`.
- `20-specs/features/` — `intrinsic-calibration-flow`, `extrinsic-calibration-flow`, `3d-extrinsic-review`, `calibration-export`, `calibration-recording`, `replay-recalibration`, `camera-detection-config`, `multi-camera-preview`, `realtime-telemetry`, `board-generation-download`, `wizard-navigation`.

## 🧪 Ancrage Caliscope (rappel)

Grounder sur le code/la doc Caliscope, pas sur des suppositions — **et préciser la version** : l'amont a divergé entre l'ancrage historique du projet (≤ v0.5.4) et la v0.11.5 (commit `ddda95b4`, vérifiée le 2026-08-06). Détail complet dans le `CLAUDE.md` racine.

Invariants (les deux époques) :
- Distorsion : modèle 5 coefficients `[k1, k2, p1, p2, k3]`.
- Unités Caliscope : boards saisis en cm, **monde en mètres** (translations TOML en m) ; `grid_count` = nombre de **vues**, pas de coins.
- Rotation stockée en Rodrigues 3-vecteur par caméra ; `cv2.Rodrigues` pour passer en 3×3.
- Extrinsèque : pairwise PnP/`stereoCalibrate`, chaînage transitif, bundle adjustment scipy sur la capture volume {caméras + points 3D}.

Ce qui a changé en v0.11.5 et touche nos comparaisons :
- Intrinsèque : désormais `CALIB_USE_INTRINSIC_GUESS` (comme nous) + sélection de frames par couverture. Le « sans aucun flag » d'ADR-0032 ne vaut que pour ≤ 0.5.4.
- Extrinsèque : filtre percentile **2.5 % par caméra intégré au pipeline** — leur RMSE affiché est post-filtre, et exprimé à la résolution des vidéos fournies (cf. ADR-0042 avant toute comparaison de chiffres).
- Contraintes de rigidité de la cible dans le BA (6 distances/marker, σ = 2 mm) et loss `soft_l1` sur la passe robuste.

Instrument de comparaison : `tools/eval_extrinsic_session.py <session_dir>` rejoue le sweep d'une session enregistrée et sort RMSE natif **et** sortie, rigidité du board (mm — le juge physique, indépendant du solveur) et distances inter-caméras.
