import { useDataChannel } from '@livekit/components-react';

import { useAppDispatch } from '@/app/hooks';
import { dataChannelMessageReceived } from '@/app/listenerMiddleware';

// Topic of the LiveKit data channel carrying telemetry — must match the service
// constant (calibration-service telemetry.py `TELEMETRY_TOPIC`).
const TELEMETRY_TOPIC = 'telemetry';

// Reused across messages (stateless decode) instead of allocating one per payload.
const textDecoder = new TextDecoder();

// The single subscription point for the LiveKit data channel (spec realtime-telemetry):
// mounted once under the RoomProvider, above the wizard, so it survives step navigation
// and reconnects. It stays deliberately dumb — decode the bytes and forward a raw action;
// all parse / narrow / routing lives in the listener middleware + messageRouter.
export function DataChannelListener() {
  const dispatch = useAppDispatch();
  useDataChannel(TELEMETRY_TOPIC, (msg) => {
    dispatch(
      dataChannelMessageReceived({
        topic: msg.topic ?? TELEMETRY_TOPIC,
        text: textDecoder.decode(msg.payload),
      }),
    );
  });
  return null;
}
