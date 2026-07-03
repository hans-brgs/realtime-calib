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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`);
  if (!response.ok) {
    throw new Error(`GET ${path} failed: ${response.status}`);
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
    throw new Error(`POST ${path} failed: ${response.status}`);
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

// Frame-server (ADR-0022): total frames of the recorded sweep, and the URL to one
// frame as JPEG (used directly as an <img> src for the Prepare scrubber).
export const fetchIntrinsicFrameCount = (camera: string): Promise<{ total: number }> =>
  getJson<{ total: number }>(`/intrinsic/${camera}/frames`);

export const intrinsicFrameUrl = (camera: string, index: number): string =>
  `${API_URL}/intrinsic/${camera}/frame/${index}`;

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

// Rigid world-frame changes on the solved array (spec 3d-extrinsic-review, mutating):
// put the origin on one group's board, or rotate the frame ±90° about an axis.
export type OrientRequest =
  | { op: 'set_origin'; group: number }
  | { op: 'rotate'; axis: 'x' | 'y' | 'z'; degrees: number };

export const orientExtrinsic = (body: OrientRequest): Promise<ExtrinsicResultPayload> =>
  postJson<ExtrinsicResultPayload>('/extrinsic/orient', body);

// Re-run the bundle adjustment from the current result (persisted observations,
// no redetection; the anchor keeps its — possibly reoriented — pose).
export const minimizeExtrinsic = (): Promise<ExtrinsicResultPayload> =>
  postJson<ExtrinsicResultPayload>('/extrinsic/minimize');

// Calibration export (spec calibration-export): the canonical Caliscope TOML is
// always written; formats adds 'aniposelib' and/or platform variants.
export interface ExportedFile {
  name: string;
  convention: string;
}

export const exportCalibration = (formats: string[]): Promise<{ files: ExportedFile[] }> =>
  postJson<{ files: ExportedFile[] }>('/export', { formats });

export const exportArchiveUrl = (): string => `${API_URL}/export/archive`;
