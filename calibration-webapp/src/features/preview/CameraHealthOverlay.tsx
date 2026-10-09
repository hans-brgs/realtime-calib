import { Center, Loader, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

import type { CameraCaptureState } from '@/features/telemetry/telemetrySlice';

// What a camera tile shows OVER its video when the camera is not streaming (#46): a
// tile in error says why and that it is being retried, instead of sitting black or
// frozen on its last frame. Live and idle cameras get nothing — the video speaks.
export function CameraHealthOverlay({ health }: { health: CameraCaptureState | undefined }) {
  if (health?.state !== 'error' && health?.state !== 'opening') return null;
  return (
    <Center
      style={{
        position: 'absolute',
        inset: 0,
        // Opaque enough to read over a frozen last frame, light enough to still show
        // which camera it was.
        background: 'rgba(0, 0, 0, 0.72)',
        padding: 16,
      }}
    >
      {health.state === 'opening' ? (
        <Stack gap={6} align="center">
          <Loader size="sm" color="gray" />
          <Text fz="0.78rem" c="dark.1">
            Opening camera…
          </Text>
        </Stack>
      ) : (
        <Stack gap={4} align="center" style={{ textAlign: 'center', maxWidth: 320 }}>
          <IconAlertTriangle size={22} color="var(--rc-error)" />
          <Text fz="0.84rem" fw={600} c="dark.0">
            Camera unavailable
          </Text>
          {health.reason && (
            <Text fz="0.74rem" c="dark.1" className="rc-tnum">
              {health.reason}
            </Text>
          )}
          <Text fz="0.69rem" c="dark.2">
            Check its cable — it is retried automatically.
          </Text>
        </Stack>
      )}
    </Center>
  );
}
