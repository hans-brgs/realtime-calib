import { describe, expect, it } from 'vitest';

import reducer, {
  CONVENTIONS,
  conventionByValue,
  conventionSelected,
} from '@/features/review3d/conventions';

describe('conventions (shared review/export selection)', () => {
  it('defaults to Y-up RH and updates on selection', () => {
    const initial = reducer(undefined, { type: 'noop' });
    expect(initial.value).toBe('yup-rh');
    const next = reducer(initial, conventionSelected('zup-lh'));
    expect(next.value).toBe('zup-lh');
  });

  it('maps display conventions to backend export formats', () => {
    expect(conventionByValue('yup-lh').exportFormat).toBe('unity');
    expect(conventionByValue('zup-lh').exportFormat).toBe('unreal');
    expect(conventionByValue('opencv').exportFormat).toBeNull(); // canonical only
    expect(conventionByValue('unknown').value).toBe('yup-rh'); // safe fallback
  });

  it('left-handed conventions carry a mirror (det -1 basis)', () => {
    for (const convention of CONVENTIONS) {
      const [a, b, c] = convention.m;
      const det =
        a[0] * (b[1] * c[2] - b[2] * c[1]) -
        a[1] * (b[0] * c[2] - b[2] * c[0]) +
        a[2] * (b[0] * c[1] - b[1] * c[0]);
      const expected = convention.value.endsWith('lh') ? -1 : 1;
      expect(det).toBeCloseTo(expected);
    }
  });
});
