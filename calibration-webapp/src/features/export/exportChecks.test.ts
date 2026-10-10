import { describe, expect, it } from 'vitest';

import type { ExportCheck } from '@/transport/httpClient';

import { formatCheckValue } from './exportChecks';

function check(id: string, value: number | null): ExportCheck {
  return { id, status: 'ok', value, thresholds: [], scope: 'internal', detail: '', items: {} };
}

describe('formatCheckValue', () => {
  it('speaks the unit each check is judged in', () => {
    expect(formatCheckValue(check('camera_error', 0.9621))).toBe('worst 0.96 px');
    expect(formatCheckValue(check('epipolar', 0.3565))).toBe('worst pair 0.36 px');
    expect(formatCheckValue(check('target_rigidity', 0.0038))).toBe('0.38 % of its size');
    expect(formatCheckValue(check('frame', 5.04))).toBe('5.0° off level');
  });

  it('says nothing for a check without a value, or no camera below the floor', () => {
    expect(formatCheckValue(check('frame', null))).toBe('');
    expect(formatCheckValue(check('frame', 0.4))).toBe('');
    expect(formatCheckValue(check('frame', 179.8))).toBe('');
    expect(formatCheckValue(check('cameras_above_floor', 0))).toBe('');
    expect(formatCheckValue(check('cameras_above_floor', 2))).toBe('2 below');
  });
});
