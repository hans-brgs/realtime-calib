// Typed HTTP client for the calibration-service API and the token server.
// URLs come from Vite env (never hardcoded), see env.d.ts.

import type {
  Board,
  BoardConfigRequest,
  BoardTarget,
  ConfigRequest,
  DetectedCamera,
  Session,
  SessionSummary,
} from '@/transport/types';

export interface TokenResponse {
  token: string;
  room: string;
  identity: string;
}

const API_URL = import.meta.env.VITE_API_URL;

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

export const fetchSession = (): Promise<Session> => getJson<Session>('/session');

export const fetchSessions = (): Promise<SessionSummary[]> =>
  getJson<SessionSummary[]>('/sessions');

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
export const setCaptureView = (view: string | null): Promise<{ view: string | null }> =>
  postJson<{ view: string | null }>('/capture/view', { view });

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

// Preview transcodes (ADR-0027): each recording is transcoded ONCE in the
// background to an H.264 mp4 re-timed CFR BY INDEX at PREVIEW_FPS — so
// index = round(video.currentTime * PREVIEW_FPS) is exact by construction.
// MUST match the backend constant (recording/preview.py).
export const PREVIEW_FPS = 30;

export interface PreviewStatus {
  state: 'missing' | 'running' | 'done' | 'failed';
  frames: number;
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

// Review metrics persisted at compute (ADR-0022, Results): coverage heatmap grid,
// Caliscope 5x5 image-coverage fraction, occupied tilt-azimuth sectors (/8), and each
// keyframe board's 4 outline corners in 3D camera coords (for the pose scene).
export interface IntrinsicMetrics {
  coverage: number[][];
  image_coverage: number;
  orientation_bins: number;
  board_quads: number[][][];
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

// Prepare-step knobs forwarded to the extrinsic compute; omitted fields use defaults.
export interface ExtrinsicComputeParams {
  stride?: number;
  max_spread_ms?: number;
  min_shared?: number;
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
  stride?: number;
}): Promise<{ total: number; groups: ExtrinsicGroup[] }> => {
  const params = new URLSearchParams();
  if (query?.max_spread_ms !== undefined) params.set('max_spread_ms', String(query.max_spread_ms));
  if (query?.stride !== undefined) params.set('stride', String(query.stride));
  const suffix = params.size > 0 ? `?${params.toString()}` : '';
  return getJson<{ total: number; groups: ExtrinsicGroup[] }>(`/extrinsic/groups${suffix}`);
};

export const extrinsicFrameUrl = (camera: string, index: number): string =>
  `${API_URL}/extrinsic/${camera}/frame/${index}`;

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
}

export const fetchExtrinsicResult = (): Promise<ExtrinsicResultPayload> =>
  getJson<ExtrinsicResultPayload>('/extrinsic/result');

// Rigid world-frame changes on the solved array (spec 3d-extrinsic-review, ADR-0026):
// set_frame puts the origin + axes on a group's board with its normal on the up axis
// (the single framing gesture); rotate turns the frame ±90° about an axis.
export type OrientRequest =
  | { op: 'set_frame'; group: number }
  | { op: 'rotate'; axis: 'x' | 'y' | 'z'; degrees: number };

export const orientExtrinsic = (body: OrientRequest): Promise<ExtrinsicResultPayload> =>
  postJson<ExtrinsicResultPayload>('/extrinsic/orient', body);

// Filter the worst-residual observations (Caliscope quality loop) + re-run the
// bundle adjustment. Repeat-safe: always re-filters from the full persisted
// observations; the anchor keeps its — possibly reoriented — pose.
export const minimizeExtrinsic = (): Promise<ExtrinsicResultPayload> =>
  postJson<ExtrinsicResultPayload>('/extrinsic/minimize');

// Operator sign-off on the solved array: advances the wizard step to Export —
// the rail follows the persisted step, so this transition IS the navigation.
export const validateExtrinsic = (): Promise<Session> =>
  postJson<Session>('/extrinsic/validate');

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

// Persist a drag-reorder (device paths in the chosen order). Unlike /cameras/config
// this keeps calibrations — only index + position-based name change.
export const reorderCameras = (devicePaths: string[]): Promise<Session> =>
  postJson<Session>('/cameras/order', { device_paths: devicePaths });

export const fetchExportTargets = (): Promise<ExportTarget[]> =>
  getJson<{ targets: ExportTarget[] }>('/export/conventions').then((r) => r.targets);

// Dry-run: the exact content each selected target would write, without touching disk.
export interface PreviewFile {
  name: string;
  language: string;
  content: string;
}

export const previewExport = (
  formats: string[],
  units: 'mm' | 'm',
): Promise<PreviewFile[]> =>
  postJson<{ files: PreviewFile[] }>('/export/preview', { formats, units }).then((r) => r.files);

// Persist the export config (units + targets) on the session (restored on reopen).
export const saveExportConfig = (formats: string[], units: 'mm' | 'm'): Promise<unknown> =>
  postJson('/export/config', { formats, units });

export const exportCalibration = (
  formats: string[],
  units: 'mm' | 'm' = 'mm', // platform JSONs only — the TOMLs keep their mm semantics
): Promise<{ files: ExportedFile[] }> =>
  postJson<{ files: ExportedFile[] }>('/export', { formats, units });

export const exportArchiveUrl = (): string => `${API_URL}/export/archive`;
