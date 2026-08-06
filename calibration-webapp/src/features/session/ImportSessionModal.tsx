import {
  Button,
  Code,
  Group,
  HoverCard,
  Modal,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { IconFileZip, IconInfoCircleFilled, IconUpload, IconX } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { NAME_RE } from '@/features/session/NewSessionModal';
import { importSessionThunk, selectRecentSessions } from '@/features/session/sessionSlice';
import { errorMessage, fetchSessionsLocation } from '@/transport/httpClient';

// Browsers are inconsistent with archive MIME types (Windows zips report
// x-zip-compressed; bare .tar/.xz often arrive with an empty type), so accept
// by MIME *and* extension — the react-dropzone Accept object matches either.
const ARCHIVE_ACCEPT = {
  'application/zip': ['.zip'],
  'application/x-zip-compressed': ['.zip'],
  'application/x-tar': ['.tar'],
  'application/gzip': ['.tar.gz', '.tgz'],
  'application/x-gzip': ['.tar.gz', '.tgz'],
  'application/x-bzip2': ['.tar.bz2'],
  'application/x-xz': ['.tar.xz'],
};

// Derive a valid session name from the archive filename (prefill only — editable,
// and the service revalidates): strip the extension, squash forbidden runs to
// '-', drop a non-alphanumeric prefix, cap at the 64-char folder-name limit.
function nameFromArchive(fileName: string): string {
  return fileName
    .replace(/\.(tar\.(gz|bz2|xz)|tgz|tar|zip)$/i, '')
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[^A-Za-z0-9]+/, '')
    .slice(0, 64);
}

interface ImportSessionModalProps {
  opened: boolean;
  onClose: () => void;
}

// Expected archive layout (the ADR-0035 import contract), shown on hover.
const ARCHIVE_TREE = `my-session.zip
├── intrinsics/          required
│   ├── cam_0.mp4
│   └── cam_1.mp4
└── extrinsics/          optional
    ├── cam_0.mp4
    ├── cam_1.mp4
    └── timestamps.csv   optional`;

function infoTooltip() {
  return (
    <Group gap="xs">
      <Title order={3}>Load From Files</Title>
      <HoverCard width={360} shadow="md" withArrow openDelay={200} closeDelay={400}>
        <HoverCard.Target>
          <IconInfoCircleFilled size={24} color="var(--rc-accent)" />
        </HoverCard.Target>
        <HoverCard.Dropdown>
          <Text fz="0.78rem" c="dark.2" mb={8}>
            Upload an archive (ZIP or tar) of pre-recorded videos, structured as:
          </Text>
          <Code block fz="0.7rem" mb={8}>
            {ARCHIVE_TREE}
          </Code>
          <Text fz="0.72rem" c="dark.2" style={{ lineHeight: 1.55 }}>
            Videos are named <Code fz="0.66rem">cam_&lt;number&gt;</Code> (.mp4 .mkv .mov .avi) —
            the number is the camera index (<Code fz="0.66rem">cam_0</Code> = anchor) and must match
            across both folders. Without <Code fz="0.66rem">timestamps.csv</Code> (Caliscope format:{' '}
            <Code fz="0.66rem">cam_id,frame_time</Code>), the extrinsic videos are assumed
            frame-synchronized. Only REAL capture timestamps are used for pairing — Caliscope&apos;s{' '}
            <Code fz="0.66rem">inferred_timestamps.csv</Code> (a synthetic uniform grid) is detected
            and ignored: the videos are then aligned Caliscope-style instead. A single wrapper
            folder inside the archive is fine.
          </Text>
        </HoverCard.Dropdown>
      </HoverCard>
    </Group>
  );
}

// Second entry mode (ADR-0035, spec replay-recalibration): upload an archive
// (ZIP or tar) of pre-recorded videos (intrinsics/cam_<n>.*, extrinsics/cam_<n>.*,
// optional Caliscope timestamps.csv). The service ingests it into a canonical
// session folder and makes it active; its step lands on intrinsic_board, so the
// rail follows the persisted step to Target Config (spec wizard-navigation).
export function ImportSessionModal({ opened, onClose }: ImportSessionModalProps) {
  const dispatch = useAppDispatch();
  const recent = useAppSelector(selectRecentSessions);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [root, setRoot] = useState('sessions');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!opened) return;
    setFile(null);
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
  const canImport = file !== null && validFormat && !existing && !submitting;

  const selectFile = (dropped: File) => {
    setFile(dropped);
    setName(nameFromArchive(dropped.name));
    setError(null);
  };

  const importZip = async () => {
    if (!canImport || file === null) return;
    setSubmitting(true);
    setError(null);
    try {
      await dispatch(importSessionThunk({ file, sessionId: trimmed })).unwrap();
      onClose();
    } catch (err) {
      setError(errorMessage(err, 'could not import the archive'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={infoTooltip()} centered size="lg">
      <Stack gap="sm">
        <Dropzone
          onDrop={(files) => files[0] && selectFile(files[0])}
          onReject={() => setError('Only a .zip or .tar archive is accepted.')}
          accept={ARCHIVE_ACCEPT}
          multiple={false}
          disabled={submitting}
          aria-label="Session archive"
        >
          <Group justify="center" gap="sm" mih={90} style={{ pointerEvents: 'none' }}>
            <Dropzone.Accept>
              <IconUpload size={28} color="var(--rc-accent)" stroke={1.6} />
            </Dropzone.Accept>
            <Dropzone.Reject>
              <IconX size={28} color="var(--rc-error)" stroke={1.6} />
            </Dropzone.Reject>
            <Dropzone.Idle>
              <IconFileZip size={28} color="var(--mantine-color-dark-2)" stroke={1.6} />
            </Dropzone.Idle>
            <div>
              <Text fz="0.88rem">
                {file
                  ? file.name
                  : 'Drop a session archive (.zip / .tar.gz) here, or click to browse'}
              </Text>
              <Text fz="0.72rem" c="dark.2" mt={3}>
                {file
                  ? `${(file.size / (1024 * 1024)).toFixed(1)} MB — drop another file to replace`
                  : 'intrinsics/cam_0.mp4 …  extrinsics/cam_0.mp4 … (+ optional timestamps.csv)'}
              </Text>
            </div>
          </Group>
        </Dropzone>
        <TextInput
          label="Session folder name"
          placeholder="prefilled from the ZIP name"
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          error={validation}
          onKeyDown={(event) => event.key === 'Enter' && void importZip()}
        />
        <Text fz="0.75rem" c="dark.2">
          Imported to <Code>{`${root}/${trimmed || '<name>'}`}</Code>
        </Text>
        {error && (
          <Text fz="0.78rem" c="var(--rc-error)">
            {error}
          </Text>
        )}
        <Button
          leftSection={<IconUpload size={16} />}
          onClick={() => void importZip()}
          disabled={!canImport}
          loading={submitting}
          mt="xs"
        >
          {submitting ? 'Importing…' : 'Import session'}
        </Button>
      </Stack>
    </Modal>
  );
}
