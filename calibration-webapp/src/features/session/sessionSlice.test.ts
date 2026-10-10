import { describe, expect, it } from 'vitest';

import sessionReducer, { refreshSession } from '@/features/session/sessionSlice';
import type { Session } from '@/transport/types';

// The quiet refresh must never flip the status: the shell renders a screen only
// while the session is 'ready', so a 'loading' flash would unmount the Extrinsic
// screen mid Stop -> transcode -> Prepare and lose its wizard state.
describe('refreshSession', () => {
  const ready = { ...sessionReducer(undefined, { type: '@@init' }), status: 'ready' as const };
  const session = { session_id: 's', step: 'extrinsic_capture', cameras: [] } as unknown as Session;

  it('keeps the status while pending', () => {
    expect(sessionReducer(ready, refreshSession.pending('id')).status).toBe('ready');
  });

  it('replaces the session once fulfilled', () => {
    const next = sessionReducer(ready, refreshSession.fulfilled(session, 'id'));
    expect(next.session).toBe(session);
    expect(next.status).toBe('ready');
  });
});
