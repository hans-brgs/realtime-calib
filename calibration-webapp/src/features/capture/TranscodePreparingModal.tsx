import { Button, Group, Loader, Modal, Text } from '@mantine/core';

interface TranscodePreparingModalProps {
  // Transcode status from usePreviewTranscode: null = closed, 'running' = spinner,
  // any other string = error message.
  status: string | null;
  onRetry: () => void;
  onClose: () => void;
  // Progress label — "Transcoding preview…" (single camera) vs "…previews…" (rig).
  label?: string;
}

// Blocking "Preparing replay" modal shown while the background H.264 transcode runs
// (ADR-0027): a spinner while running, then the error + a Retry action if it fails.
// Shared by the intrinsic (one preview) and extrinsic (per-camera previews) Stop ->
// Prepare flows; it cannot be dismissed while running (only the retry/close paths).
export function TranscodePreparingModal({
  status,
  onRetry,
  onClose,
  label = 'Transcoding preview…',
}: TranscodePreparingModalProps) {
  return (
    <Modal
      opened={status !== null}
      onClose={onClose}
      withCloseButton={status !== 'running'}
      closeOnClickOutside={false}
      closeOnEscape={false}
      centered
      title="Preparing replay"
    >
      {status === 'running' ? (
        <Group gap="sm">
          <Loader size="sm" />
          <Text fz="0.84rem" c="dark.1">
            {label}
          </Text>
        </Group>
      ) : (
        <>
          <Text fz="0.8rem" c="var(--rc-error)" mb="md">
            {status}
          </Text>
          <Group justify="flex-end">
            <Button onClick={onRetry}>Retry transcode</Button>
          </Group>
        </>
      )}
    </Modal>
  );
}
