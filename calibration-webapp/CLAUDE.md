# CLAUDE.md — calibration-webapp

> Ce fichier complète le `CLAUDE.md` racine. Il est chargé en plus quand Claude Code
> travaille dans `calibration-webapp/`. Les conventions cross-services et le workflow
> agentic (spec-first, plan-review-implement, ADR) sont définis à la racine et
> **s'appliquent ici intégralement** — ce fichier ne fait que les compléter.

---

## 🎯 Vue d'ensemble du service

Interface opérateur : un **wizard** qui guide la calibration de bout en bout (config caméras → board(s) → intrinsèque caméra par caméra → extrinsèque → revue 3D → export). Affiche les flux caméra avec overlays **burn-in** (via LiveKit), les **jauges de qualité** temps-réel (via data channel), et une **vue 3D** des frustums caméras pour la revue extrinsèque.

**Cliente sans état durable** : elle ne possède pas l'état de la session — celui-ci est détenu par le `calibration-service` et persisté sur disque (ADR-0011). Au montage, elle rehydrate depuis l'API HTTP et **reprend le wizard depuis l'état persisté**. Pas de `localStorage`.

**Compatible desktop, tablette et mobile** (ADR-0010) : responsive (reflow des tuiles caméra), tactile (cibles ≥ 44 px, `OrbitControls` pinch/rotate), portrait + paysage. La tablette est un appareil de pilotage de premier ordre (d'où le TLS par défaut, ADR-0006).

## 🏛️ Architecture interne

- **Wizard = machine à états finis** dans un `sessionSlice` Redux (ADR-0010). L'état d'étape (`step`) et ses transitions sont **centralisés** ; les composants sont des consommateurs passifs (pas de logique d'étape éparpillée).
- **Store Redux Toolkit** : `sessionSlice` (état du wizard rehydraté depuis l'API), `telemetrySlice` (jauges/métriques poussées par le data channel), `connectionSlice` (état LiveKit/HTTP), `camerasSlice` (caméras et leur statut).
- **Listener middleware + `messageRouter`** (pattern vision-webapp) : les messages du **data channel LiveKit** sont normalisés et routés par `type` vers les slices appropriés. C'est le foyer des side-effects liés au flux de télémétrie.
- **Transport** : client LiveKit (abonnement à N tracks vidéo = N tuiles + data channel) ; appels **HTTP** pour les commandes/config (détection caméras, set config, start/stop, compute, export). Cf. ADR-0004.
- **Vue 3D (R3F/drei)** : frustums caméras nommés, board scrubable, sélection d'origine, réorientation ±xyz, minimisation d'erreur. Rendu performant découplé de la réconciliation React pour les mises à jour fréquentes.

Arborescence (cible) sous `src/` :

- **`app/`** : store, listener middleware, `messageRouter`, hooks typés (`useAppDispatch`/`useAppSelector`).
- **`features/session/`** : `sessionSlice` (FSM wizard), sélecteurs d'étape, composant orchestrateur du wizard.
- **`features/cameras/`** : config caméras, réorganisation des index, statut.
- **`features/telemetry/`** : `telemetrySlice`, composants jauges (couverture, diversité, netteté, co-visibilité).
- **`features/preview/`** : tuiles LiveKit adaptatives (layout responsive), affichage du flux burn-in.
- **`features/board/`** : sélection board, visualisation + téléchargement PNG.
- **`features/review3d/`** : scène R3F (frustums, scrub, origine, réorientation).
- **`transport/`** : client LiveKit, client HTTP (API typée).
- **`components/`** : UI partagée Mantine · **`theme/`** : thème Mantine.

## 🧱 Stack et tooling

- React + **TypeScript strict** + Vite.
- **Mantine** (UI, responsive, tactile).
- **Redux Toolkit** (+ listener middleware) — typed hooks.
- **React-Three-Fiber** + **drei** (vue 3D).
- **React Compiler** activé.
- **yarn** (corepack).
- Outillage qualité : ESLint + Prettier (ou Biome), `tsc --noEmit`, tests (Vitest + Testing Library).
- **Liste exacte des dépendances : `package.json`.**

## ⚡ Commandes spécifiques au service

```bash
# Installer (depuis calibration-webapp/)
yarn install

# Dev server
yarn dev

# Build de production (servi par Caddy en prod)
yarn build

# Lint / types / tests
yarn lint
yarn tsc --noEmit
yarn test
```

En conteneur, la webapp est buildée et servie en statique par Caddy (ADR-0006) ; `yarn dev` reste pour l'itération locale.

## 📝 Conventions TypeScript / React

### Typing

- **`strict: true`** non négociable. Pas de `any` implicite ; `unknown` + narrowing plutôt que `any`.
- Types de domaine partagés (Camera, CoverageMetrics, messages data channel) modélisés explicitement, **alignés sur les entités** de `realtime-calib-doc/20-specs/entities/`.
- Les payloads du data channel sont validés/narrowés à la frontière (`messageRouter`) avant d'entrer dans le store.

### Nommage

- **Composants** : `PascalCase` · **hooks** : `useXxx` · **fichiers de composant** : `PascalCase.tsx` · **slices** : `xxxSlice.ts` · **sélecteurs** : `selectXxx`.

### State

- État serveur (session) → rehydraté depuis l'API, **jamais** dupliqué en source de vérité côté front.
- État UI volatile (sélection, hover) → store ou state local, **non persisté**.
- **Pas de `localStorage`/`sessionStorage`** pour l'état de session (ADR-0011).

## ⚡ Patterns à respecter

- **React Compiler activé → pas de mémoïsation manuelle** : ne pas ajouter `useMemo`/`useCallback`/`React.memo` par réflexe. Le compilateur s'en charge ; les ajouter manuellement nuit à la lisibilité et peut interférer.
- **R3F : allouer hors de `useFrame`** — créer les objets (`Vector3`, `Matrix4`, géométries) **une fois** (refs / hors render loop), muter dans `useFrame`. Allouer dans `useFrame` = pression GC à chaque frame.
- **R3F : bypasser la réconciliation React pour les updates haute fréquence** — manipuler les objets three via refs / `BufferGeometry` plutôt que de re-rendre l'arbre React (pattern vision-webapp pour le rendu de squelettes ; ici pour le scrub de board / frustums animés).
- **Télémétrie via le `messageRouter`** — un message data channel se route par `type` vers un slice ; ne pas câbler des listeners LiveKit ad hoc dans les composants.
- **Typed hooks** — toujours `useAppSelector`/`useAppDispatch`, jamais les hooks Redux bruts non typés.
- **Responsive d'abord** — les tuiles caméra reflow en colonne sur viewport étroit ; tester en portrait tablette. Cibles tactiles ≥ 44 px (props Mantine `size`).
- **3D tactile** — `OrbitControls` drei avec gestes (pinch zoom, rotate) ; vérifier sur tablette réelle.
- **Rehydratation au montage** — au chargement, récupérer l'état de session via l'API et positionner le wizard sur la bonne étape (reprise).

## 🚫 Patterns à éviter

- **Mémoïsation manuelle** (`useMemo`/`useCallback`/`memo`) sauf cas mesuré et justifié (React Compiler actif).
- **Allocations dans `useFrame`** (objets three, tableaux) — pré-allouer.
- **`localStorage`/`sessionStorage`** pour l'état de session — source de vérité = API/disque.
- **Logique d'étape du wizard dans les composants** — elle vit dans le `sessionSlice` (FSM).
- **Listeners LiveKit dans les composants** — passer par le listener middleware / `messageRouter`.
- **IP/URL hôte hardcodée** — via `import.meta.env.VITE_HOST_IP` (propagé au build, cf. `CLAUDE.md` racine).
- **Fetch non typé** — l'API HTTP a un client typé aligné sur les specs.
- **`any` / `as` abusifs** pour contourner le typage des payloads data channel — narrower proprement.

## 🧪 Tests

Vitest + Testing Library. Cibler : la **machine à états du wizard** (transitions valides/invalides), le **`messageRouter`** (chaque `type` route vers le bon slice), les **sélecteurs** d'étape, et la **rehydratation** depuis un état API mocké. Pour le rendu 3D, tests légers + vérification manuelle (le dire explicitement si non couvert).

## ⚙️ Configuration

Via variables d'environnement Vite (`import.meta.env.VITE_*`), injectées au build. Notamment `VITE_HOST_IP` (propagé depuis `HOST_IP` racine), URL de l'API HTTP, et paramètres LiveKit (récupération du token via le `livekit-token-server`). Ne jamais hardcoder d'IP/URL.

## 📚 Références doc

`realtime-calib-doc/` :

- `10-adr/` — transport 0004, burn-in 0003 (côté affichage), stack webapp + wizard FSM 0010, déploiement/TLS 0006, source de vérité 0011, ancre 0012 (UI de réorganisation).
- `20-specs/entities/` — `camera`, `coverage-metrics`, `calibration-session`, `camera-array-config`.
- `20-specs/features/` — `intrinsic-calibration-flow`, `extrinsic-calibration-flow` (+ specs UI à écrire : `multi-camera-preview`, `realtime-telemetry`, `board-generation-download`, `3d-extrinsic-review`, `session-persistence-resume` — cf. `roadmap.md`).
