import { describe, expect, it } from 'vitest';

import {
  buildConfigRequest,
  commonFps,
  commonResolutions,
  defaultCapture,
  offeredFps,
  outputDimensions,
} from '@/features/cameras/captureOptions';
import type { DetectedCamera } from '@/transport/types';

function cam(index: number, modes: Array<[number, number, number[]]>): DetectedCamera {
  return {
    index,
    device_path: `/dev/v4l/by-path/cam${index}`,
    device_node: `/dev/video${index}`,
    modes: modes.map(([width, height, fps]) => ({ pixel_format: 'MJPG', width, height, fps })),
  };
}

describe('captureOptions', () => {
  it('intersects resolutions across all cameras, largest first', () => {
    const cameras = [
      cam(0, [
        [1920, 1080, [60]],
        [1280, 720, [60]],
      ]),
      cam(1, [
        [1280, 720, [30]],
        [640, 480, [30]],
      ]),
    ];
    // Only 1280x720 is common to both.
    expect(commonResolutions(cameras)).toEqual([{ value: '1280x720', width: 1280, height: 720 }]);
  });

  it('returns empty when there is no shared resolution', () => {
    const cameras = [cam(0, [[1920, 1080, [60]]]), cam(1, [[1280, 720, [30]]])];
    expect(commonResolutions(cameras)).toEqual([]);
  });

  it('caps fps to what every camera supports at the resolution (slowest sensor wins)', () => {
    const cameras = [
      cam(0, [[1920, 1080, [60, 30]]]),
      cam(1, [[1920, 1080, [30]]]), // slower sensor
    ];
    expect(commonFps(cameras, 1920, 1080)).toEqual([30]);
  });

  it('offers the 60/30/15 ladder capped at the native max, even when only 60 is native', () => {
    // Camera advertises only 60 fps natively; the service paces 30/15 in software.
    const cameras = [cam(0, [[1920, 1080, [60]]])];
    expect(offeredFps(cameras, 1920, 1080)).toEqual([60, 30, 15]);
  });

  it('caps the ladder at the native max (no rate above what the sensor delivers)', () => {
    const cameras = [cam(0, [[1920, 1080, [30]]])];
    expect(offeredFps(cameras, 1920, 1080)).toEqual([30, 15]);
  });

  it('falls back to the native max when it sits below the ladder floor', () => {
    const cameras = [cam(0, [[1920, 1080, [10]]])];
    expect(offeredFps(cameras, 1920, 1080)).toEqual([10]);
  });

  it('offers nothing when the resolution is unknown', () => {
    const cameras = [cam(0, [[1920, 1080, [60]]])];
    expect(offeredFps(cameras, 1280, 720)).toEqual([]);
  });

  it('rounds output dimensions to even values', () => {
    // 1920x1080 * 1/3 = 640x360 (even).
    expect(outputDimensions(1920, 1080, 1 / 3)).toEqual({ width: 640, height: 360 });
    // 1280x720 * 0.5 = 640x360.
    expect(outputDimensions(1280, 720, 0.5)).toEqual({ width: 640, height: 360 });
    // odd intermediate rounds up to even.
    expect(outputDimensions(1280, 722, 0.5).height % 2).toBe(0);
  });

  it('default capture picks the largest common resolution and its top fps', () => {
    const cameras = [
      cam(0, [
        [1920, 1080, [30]],
        [1280, 720, [60]],
      ]),
      cam(1, [
        [1920, 1080, [30]],
        [1280, 720, [60]],
      ]),
    ];
    expect(defaultCapture(cameras)).toEqual({
      resolution: { value: '1920x1080', width: 1920, height: 1080 },
      fps: 30,
    });
    expect(defaultCapture([])).toBeNull();
  });

  it('builds a uniform config request keyed by camera order', () => {
    const cameras = [cam(0, [[1920, 1080, [30]]]), cam(1, [[1920, 1080, [30]]])];
    const request = buildConfigRequest(cameras, {
      prefix: 'cam',
      width: 1920,
      height: 1080,
      resizeFactor: 0.5,
      fps: 30,
    });
    expect(request.prefix).toBe('cam');
    expect(request.cameras).toHaveLength(2);
    expect(request.cameras[0]).toMatchObject({ index: 0, width: 1920, resize_factor: 0.5, fps: 30 });
    expect(request.cameras[1].index).toBe(1);
  });
});
