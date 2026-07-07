import { LiveKitRoom } from '@livekit/components-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

import { useAppDispatch } from '@/app/hooks';
import {
  connecting,
  connectionEstablished,
  connectionLost,
} from '@/features/connection/connectionSlice';
import { fetchToken } from '@/transport/httpClient';

// ONE LiveKit room for the whole page life (fix: streams flaky on step entry).
// Previously every screen mounted its own <LiveKitRoom>, so each navigation tore
// down and renegotiated the full WebRTC session (WS + ICE + DTLS) — a lottery
// that sometimes left the screen on "waiting for streams". The room now lives
// above the wizard: screens only consume the context (useTracks/useDataChannel)
// and navigation never touches the connection; the ADR-0021 reconciler just
// changes which cameras publish. On a token failure or an unexpected drop, the
// provider refetches a token and rejoins with a short constant backoff.
interface RoomConnection {
  serverUrl: string;
  token: string;
}

const RETRY_DELAY_MS = 4000;

export function RoomProvider({ children }: { children: ReactNode }) {
  const dispatch = useAppDispatch();
  const [connection, setConnection] = useState<RoomConnection | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Tracked so a flapping connection can't stack retry timers and a pending one
  // is cleared on unmount (it would otherwise setState on a dead component).
  const reconnectTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(reconnectTimer.current), []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    dispatch(connecting());
    fetchToken()
      .then((response) => {
        if (cancelled) return;
        setConnection({ serverUrl: import.meta.env.VITE_LIVEKIT_URL, token: response.token });
      })
      .catch(() => {
        if (cancelled) return;
        dispatch(connectionLost());
        timer = window.setTimeout(() => setAttempt((current) => current + 1), RETRY_DELAY_MS);
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [dispatch, attempt]);

  // Children always render inside the room context: the hooks simply report no
  // tracks until the connection is up, and each screen keeps its own
  // "Waiting for camera streams" placeholder.
  return (
    <LiveKitRoom
      serverUrl={connection?.serverUrl}
      token={connection?.token}
      connect={connection !== null}
      audio={false}
      video={false}
      style={{ height: '100%' }}
      onConnected={() => dispatch(connectionEstablished())}
      onDisconnected={() => {
        // Unexpected drop: rejoin with a FRESH token (the old one may be expired).
        dispatch(connectionLost());
        setConnection(null);
        window.clearTimeout(reconnectTimer.current);
        reconnectTimer.current = window.setTimeout(
          () => setAttempt((current) => current + 1),
          RETRY_DELAY_MS,
        );
      }}
    >
      {children}
    </LiveKitRoom>
  );
}
