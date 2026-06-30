import { Center, Loader, SimpleGrid, Text } from '@mantine/core';
import {
  isTrackReference,
  LiveKitRoom,
  useTracks,
  VideoTrack,
} from '@livekit/components-react';
import { Track } from 'livekit-client';
import { useEffect, useState } from 'react';

import { useAppDispatch } from '@/app/hooks';
import {
  connecting,
  connectionEstablished,
  connectionLost,
} from '@/features/connection/connectionSlice';
import { fetchToken } from '@/transport/httpClient';

// Renders one tile per published camera track. Tiles reflow into a column on
// narrow viewports (ADR-0010: responsive/tablet).
function CameraTiles() {
  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true });
  const cameras = trackRefs.filter(isTrackReference);

  if (cameras.length === 0) {
    return (
      <Center h="100%">
        <Text c="dimmed">Waiting for camera streams…</Text>
      </Center>
    );
  }

  return (
    <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="xs">
      {cameras.map((trackRef) => (
        <VideoTrack key={trackRef.publication.trackSid} trackRef={trackRef} />
      ))}
    </SimpleGrid>
  );
}

interface RoomConnection {
  serverUrl: string;
  token: string;
}

export function PreviewGrid() {
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
      onConnected={() => dispatch(connectionEstablished())}
      onDisconnected={() => dispatch(connectionLost())}
    >
      <CameraTiles />
    </LiveKitRoom>
  );
}
