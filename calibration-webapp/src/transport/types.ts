// Wire types — mirror the calibration-service HTTP schemas (snake_case on purpose).

export type WizardStep =
  | 'entry'
  | 'camera_setup'
  | 'intrinsic_board'
  | 'extrinsic_board_choice'
  | 'intrinsic_capture'
  | 'extrinsic_capture'
  | 'export';

// ADR-0019: two entry modes replace new/resume/load_intrinsic/load_full.
export type SessionMode = 'new-realtime' | 'load-from-files';

export type CameraStatus = 'detected' | 'configured' | 'intrinsic_done' | 'extrinsic_done';

export interface CameraConfig {
  index: number;
  name: string;
  prefix: string;
  device_path: string;
  device_node: string;
  width: number;
  height: number;
  resize_factor: number;
  fps: number;
  status: CameraStatus;
  // Intrinsic calibration result (null until computed).
  matrix?: number[][] | null;
  distortions?: number[] | null;
  calibration_error?: number | null;
  grid_count?: number | null;
  // Extrinsic calibration result (ADR-0023): world (anchor) -> camera pose.
  rotation?: number[] | null;
  translation?: number[] | null;
  extrinsic_error?: number | null;
}

export type BoardType = 'charuco' | 'aruco';

export interface Board {
  board_type: BoardType;
  dictionary: string;
  columns: number; // ChArUco grid (ignored for a single ArUco marker)
  rows: number;
  marker_ratio: number; // ChArUco marker/square, render-only
  marker_id: number; // ArUco single-marker id
  square_size_mm: number; // ChArUco measured square (metric scale)
  marker_size_mm: number; // measured marker side; metric scale for ArUco
  inverted: boolean;
}

export type BoardTarget = 'intrinsic' | 'extrinsic';

// Capture-view id reported to the service (POST /capture/view) so it opens only the
// cameras that view needs (ADR-0021): a wizard stage, the transient 'load' sub-flow, or
// 'idle' (explicit "release all" for a non-capturing screen). MUST match the backend
// Literal (CaptureViewRequest.view). Never null from a screen — the backend reads null
// as "not reported yet -> publish all", so a non-capturing screen sends 'idle'.
export type CaptureView =
  | 'session'
  | 'cameras'
  | 'boards'
  | 'intrinsic'
  | 'extrinsic'
  | 'export'
  | 'load'
  | 'review'
  | 'idle';

export interface BoardConfigRequest {
  target: BoardTarget;
  board: Board | null; // null = inherit the intrinsic board (extrinsic only)
}

export interface Session {
  session_id: string;
  session_dir?: string; // host-relative session folder (e.g. "sessions/default")
  step: WizardStep;
  mode: SessionMode;
  intrinsic_fps: number;
  optimization_strategy: string;
  export_units?: 'mm' | 'm'; // persisted export config (ADR-0026), restored on reopen
  export_targets?: string[];
  cameras: CameraConfig[];
  intrinsic_board: Board | null;
  extrinsic_board: Board | null;
}

export interface CameraMode {
  pixel_format: string;
  width: number;
  height: number;
  fps: number[];
}

export interface DetectedCamera {
  index: number;
  device_path: string;
  device_node: string;
  modes: CameraMode[];
}

export interface CameraConfigInput {
  index: number;
  device_path: string;
  device_node: string;
  width: number;
  height: number;
  resize_factor: number;
  fps: number;
}

export interface ConfigRequest {
  prefix: string;
  cameras: CameraConfigInput[];
}

export interface SessionSummary {
  session_id: string;
  modified_at: string; // ISO 8601 UTC
  camera_count: number;
  step: WizardStep;
  status: 'empty' | 'in_progress' | 'complete';
}
