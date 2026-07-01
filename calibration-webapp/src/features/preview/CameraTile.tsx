import {
  ConnectionQualityIndicator,
  TrackMutedIndicator,
  VideoTrack,
  type TrackReference,
} from '@livekit/components-react';
import { Box, Group, Text } from '@mantine/core';

// One camera tile (style inspired by vision-webapp): rounded video with a light
// border, a name label + health dot (top-left), and connection-quality / mute
// indicators from LiveKit (top-right). Rich health states land in F3.
// `label` overrides the track-derived name (used to reflect a pending index reorder
// before it is applied/republished). The base name is the published track name (cam_i),
// since one participant carries all tracks now (ADR-0018).
export function CameraTile({ trackRef, label }: { trackRef: TrackReference; label?: string }) {
  const name = label ?? trackRef.publication.trackName;

  // The tile fills its grid cell (no scroll); `objectFit: contain` preserves each
  // camera's real aspect ratio (16:9, 4:3, …) and letterboxes with black bars where
  // the cell shape differs — never stretching or cropping the frame.
  return (
    <Box
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        minHeight: 0,
        borderRadius: 12,
        overflow: 'hidden',
        border: '1px solid var(--mantine-color-dark-4)',
        background: 'var(--rc-page)',
      }}
    >
      <VideoTrack
        trackRef={trackRef}
        style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
      />

      <Group
        gap={6}
        wrap="nowrap"
        style={{
          position: 'absolute',
          top: 8,
          left: 8,
          background: 'rgba(0, 0, 0, 0.55)',
          borderRadius: 6,
          padding: '3px 8px',
        }}
      >
        <Box w={7} h={7} style={{ borderRadius: '50%', background: 'var(--rc-success)' }} />
        <Text fz="0.69rem" c="dark.0">
          {name}
        </Text>
      </Group>

      <Group
        gap={8}
        wrap="nowrap"
        c="white"
        style={{
          position: 'absolute',
          top: 8,
          right: 8,
          background: 'rgba(0, 0, 0, 0.6)',
          borderRadius: 8,
          padding: '2px 8px',
        }}
      >
        <ConnectionQualityIndicator participant={trackRef.participant} />
        <TrackMutedIndicator trackRef={trackRef} />
      </Group>
    </Box>
  );
}
