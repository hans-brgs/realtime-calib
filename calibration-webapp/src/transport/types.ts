// Wire types — mirror the calibration-service HTTP schemas (snake_case on purpose).

export type WizardStep =
  | 'entry'
  | 'camera_setup'
  | 'intrinsic_board'
  | 'extrinsic_board_choice'
  | 'intrinsic_capture'
  | 'extrinsic_capture'
  | 'review_3d'
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
}

export interface Session {
  session_id: string;
  step: WizardStep;
  mode: SessionMode;
  intrinsic_fps: number;
  optimization_strategy: string;
  cameras: CameraConfig[];
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
