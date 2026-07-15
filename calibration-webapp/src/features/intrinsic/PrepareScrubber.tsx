import { ActionIcon, Box, Center, Group, Slider, Text } from '@mantine/core';
import { IconPlayerPauseFilled, IconPlayerPlayFilled } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import { intrinsicPreviewUrl } from '@/transport/httpClient';

// Prepare-step replay (ADR-0027/0037): a native <video> over the CFR-retimed
// preview mp4 — frame i sits exactly at (i + 0.5) / fps, where fps is SERVED by
// the transcode status (the recording's own rate — dynamic contract), so the
// slider and trim bounds map 1:1 onto the mkv indices the compute reads and
// playback speed is true. Play loops in the trim.
interface PrepareScrubberProps {
  camera: string;
  total: number;
  fps: number; // index <-> time rate served by the transcode status
  frame: number;
  onFrame: (index: number) => void;
  trim: [number, number]; // inclusive [start, end], drawn as slider marks
}

const frameTime = (index: number, fps: number): number => (index + 0.5) / fps;

export function PrepareScrubber({
  camera,
  total,
  fps,
  frame,
  onFrame,
  trim,
}: PrepareScrubberProps) {
  const video = useRef<HTMLVideoElement>(null);
  const reported = useRef(-1);
  const [playing, setPlaying] = useState(false);
  const [start, end] = trim;
  const max = Math.max(0, total - 1);

  // Paused: the playhead follows the parent's frame (slider, trim clicks).
  useEffect(() => {
    const element = video.current;
    if (element && !playing) {
      element.currentTime = frameTime(Math.min(frame, max), fps);
    }
  }, [frame, max, playing, fps]);

  // Playing: the video clock leads; report indices up, loop inside the trim.
  useEffect(() => {
    const element = video.current;
    if (!element || !playing || total === 0) return;
    void element.play();
    const id = window.setInterval(() => {
      const index = Math.min(Math.floor(element.currentTime * fps), max);
      if (index >= end) {
        element.currentTime = frameTime(start, fps); // loop back to the trim start
        reported.current = start;
        onFrame(start);
      } else if (index !== reported.current) {
        reported.current = index;
        onFrame(index);
      }
    }, 1000 / fps);
    return () => {
      window.clearInterval(id);
      element.pause();
    };
  }, [playing, start, end, max, total, onFrame, fps]);

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
        <video
          ref={video}
          src={intrinsicPreviewUrl(camera)}
          muted
          playsInline
          preload="auto"
          onLoadedMetadata={(event) => {
            event.currentTarget.currentTime = frameTime(Math.min(frame, max), fps);
          }}
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
