import { ActionIcon, Box, Center, Group, Slider, Text } from '@mantine/core';
import { IconPlayerPauseFilled, IconPlayerPlayFilled } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { intrinsicFrameUrl } from '@/transport/httpClient';

// Prepare-step replay (ADR-0022, Option C): scrub the recorded MJPG sweep by asking the
// frame-server for one JPEG frame at a time — used directly as an <img> src, no transcode.
const PLAY_FPS = 15;

interface PrepareScrubberProps {
  camera: string;
  total: number;
  frame: number;
  onFrame: (index: number) => void;
  trim: [number, number]; // inclusive [start, end], drawn as slider marks
}

export function PrepareScrubber({ camera, total, frame, onFrame, trim }: PrepareScrubberProps) {
  const [playing, setPlaying] = useState(false);
  const [start, end] = trim;
  const max = Math.max(0, total - 1);

  // Play = advance the playhead within the trim range at a fixed rate, then loop.
  useEffect(() => {
    if (!playing || total === 0) return;
    const id = setInterval(() => onFrame(frame >= end ? start : frame + 1), 1000 / PLAY_FPS);
    return () => clearInterval(id);
  }, [playing, frame, start, end, total, onFrame]);

  if (total === 0) {
    return (
      <Center
        h="100%"
        style={{ border: '1px dashed var(--rc-border)', borderRadius: 'var(--mantine-radius-md)' }}
      >
        <Text c="dark.3" fz="0.84rem">
          No recorded frames to replay.
        </Text>
      </Center>
    );
  }

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000',
          borderRadius: 'var(--mantine-radius-md)',
          overflow: 'hidden',
        }}
      >
        <img
          src={intrinsicFrameUrl(camera, frame)}
          alt={`frame ${frame}`}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
        />
      </Box>
      <Group mt="sm" gap="sm" wrap="nowrap">
        <ActionIcon
          variant="light"
          color="violet"
          size="lg"
          aria-label={playing ? 'Pause' : 'Play'}
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? <IconPlayerPauseFilled size={16} /> : <IconPlayerPlayFilled size={16} />}
        </ActionIcon>
        <Slider
          flex={1}
          min={0}
          max={max}
          value={Math.min(frame, max)}
          onChange={(value) => {
            setPlaying(false);
            onFrame(value);
          }}
          label={null}
          color="violet"
          marks={[
            { value: start, label: 'in' },
            { value: end, label: 'out' },
          ]}
        />
        <Text className="rc-tnum" fz="0.72rem" c="dark.2" w={92} ta="right" style={{ flex: 'none' }}>
          frame {frame} / {max}
        </Text>
      </Group>
    </Box>
  );
}
