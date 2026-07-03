import {
  isTrackReference,
  LiveKitRoom,
  type TrackReference,
  useTracks,
} from '@livekit/components-react';
import { Center, Loader, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { Track } from 'livekit-client';
import { type CSSProperties, useEffect, useState } from 'react';

import { useAppDispatch } from '@/app/hooks';
import {
  connecting,
  connectionEstablished,
  connectionLost,
} from '@/features/connection/connectionSlice';
import { CameraTile } from '@/features/preview/CameraTile';
import { fetchToken } from '@/transport/httpClient';

// Resolves a track's display position + label from the operator's pending index order
// (so the preview reflects a drag-reorder before it is applied). Returns null to leave
// a track in its natural place with its published name.
export type TrackArrangement = (trackName: string) => { sortIndex: number; label: string } | null;

// Cameras are keyed by the published track name (cam_i): a single publisher
// participant carries all tracks (ADR-0018), so the participant identity no longer
// identifies a camera.
const trackName = (ref: TrackReference): string => ref.publication.trackName;

// Camera tiles. Desktop / landscape: a grid that fills the area with NO scroll
// (4 cameras -> 2x2); each tile letterboxes its frame (objectFit: contain) so the
// camera ratio is respected with black bars rather than scrolling or cropping. Phone
// / portrait: a single scrolling column of aspect-ratio tiles. See
// multi-camera-preview / wizard-navigation. Exported for screens that already have
// their own LiveKitRoom (e.g. the extrinsic capture, which shares the room with its
// telemetry listener); PreviewGrid below wraps it with a self-contained room.
export function CameraGrid({ arrange }: { arrange?: TrackArrangement }) {
  const compact = useMediaQuery('(max-width: 47.99em), (orientation: portrait)') ?? false;
  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true });
  const cameras = trackRefs.filter(isTrackReference);

  if (cameras.length === 0) {
    return (
      <Center h="100%">
        <Text c="dark.3" fz="0.84rem">
          Waiting for camera streams…
        </Text>
      </Center>
    );
  }

  // Apply the pending arrangement (reorder + relabel). Keys stay the track sid, so
  // reordering moves the video nodes without remounting them (no stream interruption).
  const tiles = cameras.map((ref) => {
    const placement = arrange?.(trackName(ref)) ?? null;
    return {
      ref,
      label: placement?.label,
      sortIndex: placement?.sortIndex ?? Number.MAX_SAFE_INTEGER,
    };
  });
  if (arrange) {
    tiles.sort((a, b) => a.sortIndex - b.sortIndex);
  }

  const cols = compact ? 1 : Math.ceil(Math.sqrt(tiles.length));
  const rows = Math.ceil(tiles.length / cols);

  const containerStyle: CSSProperties = compact
    ? { display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto' }
    : {
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gridTemplateRows: `repeat(${rows}, 1fr)`,
        gap: 12,
        height: '100%',
        overflow: 'hidden',
      };
  const cellStyle: CSSProperties = compact
    ? { width: '100%', aspectRatio: '16 / 9', flex: '0 0 auto' }
    : { minWidth: 0, minHeight: 0 };

  return (
    <div style={containerStyle}>
      {tiles.map((tile) => (
        <div key={tile.ref.publication.trackSid} style={cellStyle}>
          <CameraTile trackRef={tile.ref} label={tile.label} />
        </div>
      ))}
    </div>
  );
}

interface RoomConnection {
  serverUrl: string;
  token: string;
}

export function PreviewGrid({ arrange }: { arrange?: TrackArrangement } = {}) {
  const dispatch = useAppDispatch();
  const [connection, setConnection] = useState<RoomConnection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    dispatch(connecting());
    fetchToken()
      .then((response) => {
        if (cancelled) return;
        setConnection({ serverUrl: import.meta.env.VITE_LIVEKIT_URL, token: response.token });
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  if (error) {
    return (
      <Center h="100%">
        <Text c="red">{error}</Text>
      </Center>
    );
  }

  if (!connection) {
    return (
      <Center h="100%">
        <Loader />
      </Center>
    );
  }

  return (
    <LiveKitRoom
      serverUrl={connection.serverUrl}
      token={connection.token}
      connect
      audio={false}
      video={false}
      style={{ height: '100%' }}
      onConnected={() => dispatch(connectionEstablished())}
      onDisconnected={() => dispatch(connectionLost())}
    >
      <CameraGrid arrange={arrange} />
    </LiveKitRoom>
  );
}
