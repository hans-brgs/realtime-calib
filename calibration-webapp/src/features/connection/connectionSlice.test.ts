import { describe, expect, it } from 'vitest';

import reducer, {
  connecting,
  connectionEstablished,
  connectionLost,
} from '@/features/connection/connectionSlice';

describe('connectionSlice', () => {
  it('starts idle', () => {
    expect(reducer(undefined, { type: '@@INIT' }).status).toBe('idle');
  });

  it('moves through the connection lifecycle', () => {
    let state = reducer(undefined, connecting());
    expect(state.status).toBe('connecting');

    state = reducer(state, connectionEstablished());
    expect(state.status).toBe('connected');

    state = reducer(state, connectionLost());
    expect(state.status).toBe('disconnected');
  });
});
