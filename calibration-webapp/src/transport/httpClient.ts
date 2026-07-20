// Typed HTTP client for the calibration-service API and the token server.
// URLs come from Vite env (never hardcoded), see env.d.ts.

import type {
  Board,
  BoardConfigRequest,
  BoardTarget,
  CaptureView,
  ConfigRequest,
  DetectedCamera,
  PipelineDefaults,
  Session,
  SessionSummary,
} from '@/transport/types';

export interface TokenResponse {
  token: string;
  room: string;
  identity: string;
}

const API_URL = import.meta.env.VITE_API_URL;

// Human message from ANY rejection. Redux Toolkit's unwrap() rejects with a
// SerializedError — a plain {name, message} object, NOT an Error instance — so
// an `instanceof Error` check alone silently masks the service's detail behind
// generic fallbacks. Screens should funnel catch blocks through this.
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  if (typeof err === 'object' && err !== null && 'message' in err) {
    const message = (err as { message?: unknown }).message;
    if (typeof message === 'string' && message) {
      return message;
    }
  }
  return fallback;
}

// Surface the service's error `detail` (FastAPI) — "no camera pair shares >= 5
// board views" is actionable, "POST /extrinsic/compute failed: 422" is not.
async function errorFrom(response: Response, fallback: string): Promise<Error> {
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === 'string' && detail.length > 0) {
      return new Error(detail);
    }
  } catch {
    /* non-JSON error body: fall through to the generic message */
  }
  return new Error(fallback);
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`);
  if (!response.ok) {
    throw await errorFrom(response, `GET ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw await errorFrom(response, `POST ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

// Multipart POST (file upload). No manual Content-Type: the browser sets the
// multipart boundary itself when handed a FormData body.
async function postForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { method: 'POST', body: form });
  if (!response.ok) {
    throw await errorFrom(response, `POST ${path} failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

// null = no active session (ADR-0028): the service returns 404, the webapp lands
// on the dashboard with the wizard rail locked rather than treating it as an error.
export const fetchSession = async (): Promise<Session | null> => {
  const response = await fetch(`${API_URL}/session`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw await errorFrom(response, `GET /session failed: ${response.status}`);
  }
  return (await response.json()) as Session;
};

export const fetchSessions = (): Promise<SessionSummary[]> =>
  getJson<SessionSummary[]>('/sessions');

// Pipeline defaults and bounds — the backend is the single source (ADR-0036);
// every knob seeds its value and min/max from this payload.
export const fetchDefaults = (): Promise<PipelineDefaults> =>
  getJson<PipelineDefaults>('/defaults');

// Rig-level operator settings (ADR-0036), persisted service-side in
// settings.toml. PUT is full-replace (always send the complete object); changes
// apply live (publication pacer swaps on the next frame; recording quality on
// the next recording). preview_fps: null = follow the camera fps.
export interface Settings {
  record_quality: number;
  preview_fps: number | null;
}

export const fetchSettings = (): Promise<Settings> => getJson<Settings>('/settings');

export const saveSettings = async (settings: Settings): Promise<Settings> => {
  const response = await fetch(`${API_URL}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!response.ok) {
    throw await errorFrom(response, `PUT /settings failed: ${response.status}`);
  }
  return (await response.json()) as Settings;
};

// Create a fresh session (unique folder name = id) and make it active. Its wizard
// step is intrinsic_board, so the rail auto-navigates to Target Config.
export const createSession = (sessionId: string): Promise<Session> =>
  postJson<Session>('/sessions', { session_id: sessionId });

// Switch the active session to an existing one (Recent sessions / resume).
export const openSession = (sessionId: string): Promise<Session> =>
  postJson<Session>('/sessions/open', { session_id: sessionId });

// Host-relative sessions root (e.g. "sessions"), shown live in the create popup.
export const fetchSessionsLocation = (): Promise<string> =>
  getJson<{ root: string }>('/sessions/location').then((r) => r.root);

// Import a pre-recorded session archive, ZIP or tar (ADR-0031): the service
// ingests it into a fresh session folder (extract, validate, remux to the
// canonical layout) and makes it the active session, landing on Target Config.
// 409 if the name exists, 422 on a contract violation, 400 if not zip/tar.
export const importSession = (file: File, sessionId: string): Promise<Session> => {
  const form = new FormData();
  form.append('file', file);
  form.append('session_id', sessionId);
  return postForm<Session>('/sessions/import', form);
};

export const detectCameras = (): Promise<DetectedCamera[]> =>
  postJson<DetectedCamera[]>('/cameras/detect');

export const configureCameras = (request: ConfigRequest): Promise<Session> =>
  postJson<Session>('/cameras/config', request);

export const fetchBoardDictionaries = (): Promise<string[]> =>
  getJson<string[]>('/board/dictionaries');

export const setActiveIntrinsic = (camera: string | null): Promise<{ active: string | null }> =>
  postJson<{ active: string | null }>('/intrinsic/active', { camera });

// Report the operator's current wizard view so the service captures only the cameras
// that view needs (ADR-0021: cameras/extrinsic -> all, intrinsic -> active, else none).
export const setCaptureView = (view: CaptureView | null): Promise<{ view: CaptureView | null }> =>
  postJson<{ view: CaptureView | null }>('/capture/view', { view });

export const startIntrinsic = (camera: string): Promise<{ recording: string }> =>
  postJson<{ recording: string }>(`/intrinsic/${camera}/start`);

export const stopIntrinsic = (camera: string): Promise<{ camera: string; frames: number }> =>
  postJson<{ camera: string; frames: number }>(`/intrinsic/${camera}/stop`);

// Prepare-step knobs forwarded to the compute (ADR-0022); omitted fields use auto/defaults.
export interface ComputeParams {
  stride?: number;
  cap?: number;
  frame_start?: number;
  frame_end?: number;
}

export const computeIntrinsic = (camera: string, params?: ComputeParams): Promise<Session> =>
  postJson<Session>(`/intrinsic/${camera}/compute`, params);

// Operator sign-off once every camera has intrinsics: advances the wizard step to
// extrinsic capture — the rail follows the persisted step, so this IS the navigation.
export const validateIntrinsic = (): Promise<Session> => postJson<Session>('/intrinsic/validate');

// Preview transcodes (ADR-0027/0037): each recording is transcoded ONCE in the
// background to an H.264 mp4 re-timed CFR BY INDEX at the recording's own fps.
// The rate is SERVED in the status (dynamic contract — no hardcoded copy):
// index = round(video.currentTime * status.fps) is exact by construction, and
// playback speed is true whatever the configured capture rate.
export interface PreviewStatus {
  state: 'missing' | 'running' | 'done' | 'failed';
  frames: number;
  fps: number; // index <-> time rate of the DONE preview
  // Identity of the DONE mp4 — appended to the preview URL as a cache-buster so a
  // re-recorded sweep is never scrubbed against a browser-cached stale video
  // (trim bounds set on the wrong timeline would silently mis-trim the compute).
  version: string;
  error: string | null;
}

export const intrinsicPreviewUrl = (camera: string): string =>
  `${API_URL}/intrinsic/${camera}/preview`;

export const fetchIntrinsicPreviewStatus = (camera: string): Promise<PreviewStatus> =>
  getJson<PreviewStatus>(`/intrinsic/${camera}/preview/status`);

export const retryIntrinsicPreview = (camera: string): Promise<PreviewStatus> =>
  postJson<PreviewStatus>(`/intrinsic/${camera}/preview/transcode`);

export interface ExtrinsicPreviewStatus {
  state: 'missing' | 'running' | 'done' | 'failed';
  cameras: Record<string, PreviewStatus>;
}

export const extrinsicPreviewUrl = (camera: string): string =>
  `${API_URL}/extrinsic/${camera}/preview`;

export const fetchExtrinsicPreviewStatus = (): Promise<ExtrinsicPreviewStatus> =>
  getJson<ExtrinsicPreviewStatus>('/extrinsic/preview/status');

export const retryExtrinsicPreview = (): Promise<ExtrinsicPreviewStatus> =>
  postJson<ExtrinsicPreviewStatus>('/extrinsic/preview/transcode');

// Review metrics persisted at compute (ADR-0022/0038/0039, Results): the coverage
// map is a quad-accumulation COUNT per cell (how many retained keyframes' board
// hulls covered it — 0 never, 1 fragile, 3+ robust); image_coverage is the
// union-of-quads area fraction (grid-free); orientation_bins the occupied
// tilt-azimuth sectors (/8); board_quads each keyframe board's 4 outline corners
// in 3D camera coords. sharpness_min/median describe the retained keyframes — the
// observability that replaced the absolute blur gate (absent on metrics persisted
// before ADR-0038).
export interface IntrinsicMetrics {
  coverage: number[][];
  image_coverage: number;
  orientation_bins: number;
  board_quads: number[][][];
  sharpness_min?: number;
  sharpness_median?: number;
}

export const fetchIntrinsicMetrics = (camera: string): Promise<IntrinsicMetrics> =>
  getJson<IntrinsicMetrics>(`/intrinsic/${camera}/metrics`);

export const defineBoard = (request: BoardConfigRequest): Promise<Session> =>
  postJson<Session>('/board', request);

// Persisted board PNG (physical print), usable directly as an <img>/download href.
export const boardImageUrl = (target: BoardTarget): string =>
  `${API_URL}/board/${target}/image.png`;

// Render an unsaved board to a PNG blob (live preview), same engine as the download.
export async function previewBoard(board: Board): Promise<Blob> {
  const response = await fetch(`${API_URL}/board/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(board),
  });
  if (!response.ok) {
    throw new Error(`board preview failed: ${response.status}`);
  }
  return response.blob();
}

export async function fetchToken(): Promise<TokenResponse> {
  const response = await fetch(import.meta.env.VITE_TOKEN_URL);
  if (!response.ok) {
    throw new Error(`token request failed: ${response.status}`);
  }
  return (await response.json()) as TokenResponse;
}

// --- Extrinsic sweep (ADR-0023) -----------------------------------------------

export const startExtrinsic = (): Promise<{ recording: boolean; cameras: number }> =>
  postJson<{ recording: boolean; cameras: number }>('/extrinsic/start');

export const stopExtrinsic = (): Promise<{ frames: Record<string, number> }> =>
  postJson<{ frames: Record<string, number> }>('/extrinsic/stop');

// Prepare-step knobs forwarded to the extrinsic compute; omitted fields resolve
// backend-side against TUNING (ADR-0036). `min_shared` is deliberately absent:
// it is an API-only knob since ADR-0036 (possible UI reintegration later, under
// an Advanced section).
export interface ExtrinsicComputeParams {
  // Detection stride over the spread-filtered candidate groups ("1 group / N").
  stride?: number;
  // Sharpest groups kept for the solve (ADR-0033); omitted = board-type default.
  max_groups?: number;
  max_spread_ms?: number;
}

export const computeExtrinsic = (params?: ExtrinsicComputeParams): Promise<Session> =>
  postJson<Session>('/extrinsic/compute', params);

// One synchronized group of the recorded sweep: per-camera frame index + timestamp
// spread. What the Prepare scrubber lists is exactly what the compute consumes.
export interface ExtrinsicGroup {
  frames: Record<string, number>;
  spread_ms: number;
}

export const fetchExtrinsicGroups = (query?: {
  max_spread_ms?: number;
}): Promise<{ total: number; groups: ExtrinsicGroup[] }> => {
  const params = new URLSearchParams();
  if (query?.max_spread_ms !== undefined) params.set('max_spread_ms', String(query.max_spread_ms));
  const suffix = params.size > 0 ? `?${params.toString()}` : '';
  return getJson<{ total: number; groups: ExtrinsicGroup[] }>(`/extrinsic/groups${suffix}`);
};

// Persisted array solve (poses + errors + 3D review scene data), for the Result
// view + reload. Points carry their synchronized-group index (scrub); board_quads
// give each group's board outline in world coords (corner order c0..c3 lets the
// scene derive the board's local xyz triad).
export interface ExtrinsicResultPayload {
  cameras: string[];
  rotations: Record<string, number[]>;
  translations: Record<string, number[]>;
  per_camera_error: Record<string, number>;
  error: number;
  pair_errors: Record<string, number>;
  group_count: number;
  point_count: number;
  points: number[][];
  point_groups: number[];
  board_quads: (number[][] | null)[];
  // Group that received the "set frame" gesture (review-scrubber marker);
  // null/absent until the gesture, reset by a fresh solve.
  framed_group?: number | null;
  // Bundle-adjustment diagnostics (ADR-0036): ba_converged false = scipy hit its
  // evaluation ceiling, so the poses are best-so-far, not a converged optimum.
  // observations_used/_total report the Minimize outlier filter (equal on a full
  // solve). Absent on results persisted before ADR-0036.
  ba_converged?: boolean;
  ba_nfev?: number;
  observations_used?: number;
  observations_total?: number;
}

export const fetchExtrinsicResult = (): Promise<ExtrinsicResultPayload> =>
  getJson<ExtrinsicResultPayload>('/extrinsic/result');

// Rigid world-frame changes on the solved array (spec 3d-extrinsic-review, ADR-0026):
// set_frame puts the origin + axes on a group's board with its normal on the up axis
// (the single framing gesture); rotate turns the frame ±90° about an axis.
export type OrientRequest =
  { op: 'set_frame'; group: number } | { op: 'rotate'; axis: 'x' | 'y' | 'z'; degrees: number };

export const orientExtrinsic = (body: OrientRequest): Promise<ExtrinsicResultPayload> =>
  postJson<ExtrinsicResultPayload>('/extrinsic/orient', body);

// Filter the worst-residual observations (Caliscope quality loop) + re-run the
// bundle adjustment. Repeat-safe: always re-filters from the full persisted
// observations; the anchor keeps its — possibly reoriented — pose.
export const minimizeExtrinsic = (): Promise<ExtrinsicResultPayload> =>
  postJson<ExtrinsicResultPayload>('/extrinsic/minimize');

// Operator sign-off on the solved array: advances the wizard step to Export —
// the rail follows the persisted step, so this transition IS the navigation.
export const validateExtrinsic = (): Promise<Session> => postJson<Session>('/extrinsic/validate');

// Calibration export (spec calibration-export, ADR-0026). Targets are all optional
// ('caliscope' TOML + platform JSONs); the backend owns the catalog and preview.
export interface ExportedFile {
  name: string;
  convention: string;
}

// One selectable target from the backend catalog (GET /export/conventions).
export interface ExportTarget {
  id: string;
  filename: string;
  kind: 'toml' | 'json';
  label: string;
  up: string;
  handedness: string;
}

// Advance past Camera Setup without touching the configs (ADR-0040: the only way
// forward — /cameras/config applies but never advances). Also the load-from-files
// "Continue" (the cameras derive from the imported videos).
export const confirmCameraSetup = (): Promise<Session> => postJson<Session>('/cameras/confirm');

export const fetchExportTargets = (): Promise<ExportTarget[]> =>
  getJson<{ targets: ExportTarget[] }>('/export/conventions').then((r) => r.targets);

// Dry-run: the exact content each selected target would write, without touching disk.
export interface PreviewFile {
  name: string;
  language: string;
  content: string;
}

export const previewExport = (formats: string[], units: 'mm' | 'm'): Promise<PreviewFile[]> =>
  postJson<{ files: PreviewFile[] }>('/export/preview', { formats, units }).then((r) => r.files);

// Persist the export config (units + targets) on the session (restored on reopen).
export const saveExportConfig = (formats: string[], units: 'mm' | 'm'): Promise<unknown> =>
  postJson('/export/config', { formats, units });

export const exportCalibration = (
  formats: string[],
  units: 'mm' | 'm', // explicit — the session preference is the backend's fallback
): Promise<{ files: ExportedFile[] }> =>
  postJson<{ files: ExportedFile[] }>('/export', { formats, units });

export const exportArchiveUrl = (): string => `${API_URL}/export/archive`;
