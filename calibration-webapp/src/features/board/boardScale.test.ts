import { describe, expect, it } from 'vitest';

import { boardScaleRoles } from './boardScale';

describe('boardScaleRoles', () => {
  it('treats the intrinsic board as the scale only while the extrinsic step inherits it', () => {
    expect(boardScaleRoles('intrinsic', false).edited).toBe(true);
    // A separate extrinsic target carries the scale instead, so the intrinsic
    // board's printed size stops mattering — the whole point of the rule: the
    // intrinsic solve never reads it.
    expect(boardScaleRoles('intrinsic', true).edited).toBe(false);
  });

  it('always treats a separate extrinsic board as the scale', () => {
    expect(boardScaleRoles('extrinsic', true).edited).toBe(true);
  });

  it('keeps the preview assertive on the extrinsic tab while inheriting', () => {
    // Nothing is editable there (the tab saves board=null), but the preview
    // shows the intrinsic board — which IS the extrinsic target in that mode.
    const roles = boardScaleRoles('extrinsic', false);
    expect(roles.edited).toBe(false);
    expect(roles.previewed).toBe(true);
  });

  it('only silences the preview for an intrinsic board the extrinsic step will not use', () => {
    expect(boardScaleRoles('intrinsic', true).previewed).toBe(false);
    expect(boardScaleRoles('intrinsic', false).previewed).toBe(true);
    expect(boardScaleRoles('extrinsic', true).previewed).toBe(true);
  });
});
