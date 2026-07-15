// Pure helpers for the shared capture configuration (spec camera-detection-config):
// the resolution / fps offered to the operator is the INTERSECTION of what every
// detected camera supports, and the resize factor `s` derives an even output size
// (K_out = s·K applies on export, ADR-0015). No React here — unit-tested.

import type { CameraConfigInput, ConfigRequest, DetectedCamera } from '@/transport/types';

export interface ResolutionOption {
  value: string; // "WxH"
  width: number;
  height: number;
}

const resKey = (width: number, height: number): string => `${width}x${height}`;

export function parseResolution(value: string): { width: number; height: number } {
  const [width, height] = value.split('x').map(Number);
  return { width, height };
}

// Round to the nearest even integer (VP8/H264 encoders require even dimensions).
function roundEven(n: number): number {
  const r = Math.round(n);
  return r % 2 === 0 ? r : r + 1;
}

export function outputDimensions(
  width: number,
  height: number,
  factor: number,
): { width: number; height: number } {
  return { width: roundEven(width * factor), height: roundEven(height * factor) };
}

// Resolutions supported by EVERY camera, largest first.
export function commonResolutions(cameras: DetectedCamera[]): ResolutionOption[] {
  if (cameras.length === 0) {
    return [];
  }
  const perCamera = cameras.map((c) => new Set(c.modes.map((m) => resKey(m.width, m.height))));
  const [first, ...rest] = perCamera;
  return [...first]
    .filter((key) => rest.every((set) => set.has(key)))
    .map((key) => ({ value: key, ...parseResolution(key) }))
    .sort((a, b) => b.width * b.height - a.width * a.height);
}

// Integer fps supported by EVERY camera at the given resolution, highest first
// (the top value is implicitly capped by the slowest sensor).
export function commonFps(cameras: DetectedCamera[], width: number, height: number): number[] {
  if (cameras.length === 0) {
    return [];
  }
  const perCamera = cameras.map((c) => {
    const set = new Set<number>();
    for (const mode of c.modes) {
      if (mode.width === width && mode.height === height) {
        for (const f of mode.fps) {
          set.add(Math.floor(f));
        }
      }
    }
    return set;
  });
  const [first, ...rest] = perCamera;
  return [...first].filter((f) => rest.every((set) => set.has(f))).sort((a, b) => b - a);
}

// Capture rates offered to the operator, fastest first. The ladder comes from the
// backend-served defaults (GET /defaults, ADR-0036) — its max IS the recording
// cap. A rate below what the sensor advertises natively is still offered: the
// service honours it by pacing the pipeline (dropping surplus frames).
export function offeredFps(
  cameras: DetectedCamera[],
  width: number,
  height: number,
  ladder: readonly number[],
): number[] {
  const native = commonFps(cameras, width, height);
  if (native.length === 0) {
    return [];
  }
  const max = native[0]; // commonFps is sorted high-to-low
  const offered = ladder.filter((f) => f <= max);
  return offered.length > 0 ? offered : [max];
}

export interface CaptureDefaults {
  resolution: ResolutionOption;
  fps: number;
}

export function defaultCapture(
  cameras: DetectedCamera[],
  opts: { fpsOptions: readonly number[]; defaultFps: number },
): CaptureDefaults | null {
  const resolutions = commonResolutions(cameras);
  if (resolutions.length === 0) {
    return null;
  }
  const top = resolutions[0];
  const fps = offeredFps(cameras, top.width, top.height, opts.fpsOptions);
  return { resolution: top, fps: fps[0] ?? opts.defaultFps };
}

// Build the (uniform) config request: the same resolution / factor / fps applied to
// every camera, in their current order (index = position).
export function buildConfigRequest(
  cameras: DetectedCamera[],
  params: { prefix: string; width: number; height: number; resizeFactor: number; fps: number },
): ConfigRequest {
  const configs: CameraConfigInput[] = cameras.map((camera, index) => ({
    index,
    device_path: camera.device_path,
    device_node: camera.device_node,
    width: params.width,
    height: params.height,
    resize_factor: params.resizeFactor,
    fps: params.fps,
  }));
  return { prefix: params.prefix, cameras: configs };
}
