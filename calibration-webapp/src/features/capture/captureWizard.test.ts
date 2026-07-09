import { describe, expect, it } from 'vitest';

import {
  captureWizardReducer,
  initialCaptureWizardState,
} from '@/features/capture/captureWizard';

const initial = initialCaptureWizardState('capture');

describe('captureWizardReducer', () => {
  it('START begins recording on the capture step and clears a stale message', () => {
    const next = captureWizardReducer({ ...initial, message: 'old error' }, { type: 'START' });
    expect(next).toMatchObject({ step: 'capture', recording: true, message: null });
  });

  it('START_FAILED surfaces the error without recording', () => {
    const next = captureWizardReducer(initial, { type: 'START_FAILED', message: 'device busy' });
    expect(next).toMatchObject({ recording: false, message: 'device busy' });
  });

  it('STOP then PREVIEW_READY stops recording and enters Prepare', () => {
    let next = captureWizardReducer({ ...initial, recording: true }, { type: 'STOP' });
    expect(next.recording).toBe(false);
    expect(next.step).toBe('capture'); // stays on capture until the preview is ready
    next = captureWizardReducer(next, { type: 'PREVIEW_READY' });
    expect(next.step).toBe('prepare');
  });

  it('COMPUTE -> COMPUTE_OK walks prepare -> computing -> review', () => {
    let next = captureWizardReducer({ ...initial, step: 'prepare' }, { type: 'COMPUTE' });
    expect(next.step).toBe('computing');
    next = captureWizardReducer(next, { type: 'COMPUTE_OK' });
    expect(next.step).toBe('review');
  });

  it('COMPUTE_FAILED returns to Prepare with the error', () => {
    const next = captureWizardReducer(
      { ...initial, step: 'computing' },
      { type: 'COMPUTE_FAILED', message: 'no keyframes' },
    );
    expect(next).toMatchObject({ step: 'prepare', message: 'no keyframes' });
  });

  // ADR-0019 overwrite guard — the core regression risk of this refactor. Re-record is
  // only reachable from Review (a result always exists), so it ALWAYS confirms first.
  it('RERECORD opens the overwrite modal, keeping the step', () => {
    const next = captureWizardReducer({ ...initial, step: 'review' }, { type: 'RERECORD' });
    expect(next).toMatchObject({ step: 'review', overwriteOpen: true });
  });

  it('CONFIRM_RERECORD closes the modal and restarts at capture', () => {
    const next = captureWizardReducer(
      { ...initial, step: 'review', overwriteOpen: true },
      { type: 'CONFIRM_RERECORD' },
    );
    expect(next).toMatchObject({ step: 'capture', overwriteOpen: false, recording: false });
  });

  it('CANCEL_OVERWRITE closes the modal and keeps the review step', () => {
    const next = captureWizardReducer(
      { ...initial, step: 'review', overwriteOpen: true },
      { type: 'CANCEL_OVERWRITE' },
    );
    expect(next).toMatchObject({ step: 'review', overwriteOpen: false });
  });

  it('RESET jumps to a step and stops recording (intrinsic camera switch)', () => {
    const next = captureWizardReducer(
      { ...initial, recording: true },
      { type: 'RESET', step: 'review' },
    );
    expect(next).toMatchObject({ step: 'review', recording: false });
  });
});
