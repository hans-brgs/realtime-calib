import { configureStore } from '@reduxjs/toolkit';
import { afterEach, describe, expect, it, vi } from 'vitest';

import sessionReducer, { applyBoardConfig } from '@/features/session/sessionSlice';
import { DISCARDS_EXTRINSIC, defineBoard, errorCode, errorMessage } from '@/transport/httpClient';

// A refusal the screen must recognise (ADR-0048): the service answers 409 with a
// structured detail; the code has to reach the catch block of the caller.
function respondWith(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), { status })),
  );
}

describe('structured API errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the code and the message of a structured detail', async () => {
    respondWith(409, { detail: { code: DISCARDS_EXTRINSIC, message: 'would discard the solve' } });
    const failure = await defineBoard({ target: 'extrinsic', board: null }).catch(
      (err: unknown) => err,
    );
    expect(errorCode(failure)).toBe(DISCARDS_EXTRINSIC);
    expect(errorMessage(failure, 'fallback')).toBe('would discard the solve');
  });

  it('still surfaces a plain string detail, without a code', async () => {
    respondWith(422, { detail: 'no extrinsic board defined' });
    const failure = await defineBoard({ target: 'extrinsic', board: null }).catch(
      (err: unknown) => err,
    );
    expect(errorCode(failure)).toBeUndefined();
    expect(errorMessage(failure, 'fallback')).toBe('no extrinsic board defined');
  });

  it('keeps the code through a Redux Toolkit thunk and unwrap()', async () => {
    // The screens catch what unwrap() rethrows: the serialized rejection.
    respondWith(409, { detail: { code: DISCARDS_EXTRINSIC, message: 'would discard the solve' } });
    const store = configureStore({ reducer: { session: sessionReducer } });
    const failure = await store
      .dispatch(applyBoardConfig({ target: 'extrinsic', board: null }))
      .unwrap()
      .catch((err: unknown) => err);
    expect(errorCode(failure)).toBe(DISCARDS_EXTRINSIC);
    expect(errorMessage(failure, 'fallback')).toBe('would discard the solve');
  });
});
