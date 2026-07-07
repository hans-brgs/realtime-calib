import { Button, Code, Modal, Stack, Text, TextInput } from '@mantine/core';
import { IconFolderPlus } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { createSessionThunk, selectRecentSessions } from '@/features/session/sessionSlice';
import { fetchSessionsLocation } from '@/transport/httpClient';

// Mirror of the service-side rule (calibration_service.session.manager, ADR-0028):
// the id becomes a folder name — first char alphanumeric, then [A-Za-z0-9._-],
// max 64. Client-side validation is UX only; the service stays the source of truth.
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

interface NewSessionModalProps {
  opened: boolean;
  onClose: () => void;
}

// Create a new realtime session: pick a unique folder name (= session id) and start
// the wizard from scratch. On success the session's step is intrinsic_board, so the
// rail follows the persisted step and navigates to Target Config (spec wizard-navigation).
export function NewSessionModal({ opened, onClose }: NewSessionModalProps) {
  const dispatch = useAppDispatch();
  const recent = useAppSelector(selectRecentSessions);
  const [name, setName] = useState('');
  const [root, setRoot] = useState('sessions');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!opened) return;
    setName('');
    setError(null);
    fetchSessionsLocation()
      .then(setRoot)
      .catch(() => {});
  }, [opened]);

  const trimmed = name.trim();
  const existing = recent.some((s) => s.session_id === trimmed);
  const validFormat = NAME_RE.test(trimmed);
  const validation =
    trimmed === ''
      ? null
      : !validFormat
        ? 'Use letters, digits, . _ - — start with a letter or digit (max 64).'
        : existing
          ? 'A session with this name already exists.'
          : null;
  const canCreate = validFormat && !existing && !submitting;

  const create = async () => {
    if (!canCreate) return;
    setSubmitting(true);
    setError(null);
    try {
      await dispatch(createSessionThunk(trimmed)).unwrap();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'could not create the session');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="New realtime calibration" centered>
      <Stack gap="sm">
        <TextInput
          label="Session folder name"
          placeholder="e.g. mocap-2026-07-07"
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          error={validation}
          data-autofocus
          onKeyDown={(event) => event.key === 'Enter' && void create()}
        />
        <Text fz="0.75rem" c="dark.2">
          Created at <Code>{`${root}/${trimmed || '<name>'}`}</Code>
        </Text>
        {error && (
          <Text fz="0.78rem" c="var(--rc-error)">
            {error}
          </Text>
        )}
        <Button
          leftSection={<IconFolderPlus size={16} />}
          onClick={() => void create()}
          disabled={!canCreate}
          loading={submitting}
          mt="xs"
        >
          Create session
        </Button>
      </Stack>
    </Modal>
  );
}
