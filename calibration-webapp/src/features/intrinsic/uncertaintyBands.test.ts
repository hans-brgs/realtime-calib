import { describe, expect, it } from 'vitest';

import { formatUncertainty, UNCERTAINTY_BANDS, uncertaintyBand } from './uncertaintyBands';

describe('uncertaintyBand', () => {
  it('reads each band up to its inclusive bound', () => {
    expect(uncertaintyBand(0.2).label).toBe('≤ 0.5');
    expect(uncertaintyBand(0.5).label).toBe('≤ 0.5');
    expect(uncertaintyBand(0.51).label).toBe('≤ 1');
    expect(uncertaintyBand(1.9).label).toBe('≤ 2');
    expect(uncertaintyBand(4.0).label).toBe('≤ 5');
  });

  it('puts anything past the last bound in the last band', () => {
    expect(uncertaintyBand(40)).toBe(UNCERTAINTY_BANDS[UNCERTAINTY_BANDS.length - 1]);
  });

  it('keeps the bands ordered', () => {
    const bounds = UNCERTAINTY_BANDS.map((band) => band.max);
    expect([...bounds].sort((a, b) => a - b)).toEqual(bounds);
  });
});

describe('formatUncertainty', () => {
  it('shows covered / never covered at one decimal', () => {
    expect(formatUncertainty(0.64, 4.25)).toBe('0.6 / 4.3 px');
  });

  it('caps a figure too large to read', () => {
    expect(formatUncertainty(0.6, 257)).toBe('0.6 / > 100 px');
  });

  it('dashes a side the solve could not read, and both when nothing is known', () => {
    expect(formatUncertainty(0.6, null)).toBe('0.6 / — px');
    expect(formatUncertainty(undefined, undefined)).toBe('—');
  });
});
