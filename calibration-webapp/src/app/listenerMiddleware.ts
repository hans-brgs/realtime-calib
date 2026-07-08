import { createAction, createListenerMiddleware } from '@reduxjs/toolkit';

import { routeDataChannelMessage } from '@/app/messageRouter';

// Raw inbound data-channel message, dispatched by the single DataChannelListener bridge
// (spec realtime-telemetry). It carries the already-decoded text (not the raw bytes) so
// the action stays serializable; `topic` is kept for future topic-aware routing.
export const dataChannelMessageReceived = createAction<{ topic: string; text: string }>(
  'dataChannel/messageReceived',
);

// The listener middleware is the centralized home for data-channel side-effects: it
// reacts to every raw inbound message, routes it by `type` (messageRouter), and
// dispatches the resulting slice action. This is the only wiring between the channel
// and the store — screens stay passive consumers of telemetrySlice.
export const listenerMiddleware = createListenerMiddleware();

listenerMiddleware.startListening({
  actionCreator: dataChannelMessageReceived,
  effect: (action, api) => {
    const routed = routeDataChannelMessage(action.payload.text);
    if (routed) {
      api.dispatch(routed);
    }
  },
});
