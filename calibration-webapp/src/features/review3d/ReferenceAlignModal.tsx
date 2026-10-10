import {
  Alert,
  Button,
  FileButton,
  Group,
  Modal,
  SegmentedControl,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconFileImport } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import { defaultMode, reportRows, residualRows } from '@/features/review3d/referenceReport';
import {
  type AlignmentMode,
  type ExtrinsicResultPayload,
  type ReferenceState,
  deleteReference,
  errorMessage,
  fetchReference,
  orientExtrinsic,
  putReference,
} from '@/transport/httpClient';

// Re-align the world on a reference calibration (ADR-0061): deposit a previous
// camera_array_opencv.json (or the bare {cameras: [{port, R, t}]} it grew from), read
// what each mode would do, then apply. Nothing moves before "Align".
export function ReferenceAlignModal({
  opened,
  onClose,
  onResult,
}: {
  opened: boolean;
  onClose: () => void;
  onResult: (updated: ExtrinsicResultPayload) => void;
}) {
  const [state, setState] = useState<ReferenceState | null>(null);
  const [mode, setMode] = useState<AlignmentMode>('floor');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cleared after each pick, so choosing the same file again (corrected) re-uploads.
  const resetFile = useRef<() => void>(null);

  const show = (next: ReferenceState | null) => {
    setState(next);
    if (next) setMode(defaultMode(next.preview));
  };

  useEffect(() => {
    if (!opened) return;
    let alive = true;
    setError(null);
    fetchReference()
      .then((next) => alive && show(next))
      .catch(
        (cause: unknown) => alive && setError(errorMessage(cause, 'failed to load the reference')),
      );
    return () => {
      alive = false;
    };
  }, [opened]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(errorMessage(cause, 'reference action failed'));
    } finally {
      setBusy(false);
    }
  };

  const upload = (file: File | null) => {
    if (!file) return;
    void run(async () => {
      let document: unknown;
      try {
        document = JSON.parse(await file.text());
      } catch {
        throw new Error(`${file.name} is not JSON: a reference is a camera_array_opencv.json`);
      }
      show(await putReference(file.name, document));
    }).finally(() => resetFile.current?.());
  };

  const report = state?.preview[mode];

  return (
    <Modal opened={opened} onClose={onClose} title="Align on a reference" centered size="md">
      <Stack gap="sm">
        <Text fz="0.74rem" c="dark.2">
          Keeps the room&apos;s landmarks across recalibrations. Laying the target back on a
          footprint marked on the floor reproduces the frame better; this is the fallback.
        </Text>
        <Group justify="space-between" wrap="nowrap">
          <Text fz="0.76rem" truncate>
            {state
              ? `${state.reference.name} · ${state.reference.cameras.length} cameras`
              : 'No reference loaded'}
          </Text>
          <FileButton onChange={upload} accept="application/json,.json" resetRef={resetFile}>
            {(props) => (
              <Button
                {...props}
                variant="light"
                leftSection={<IconFileImport size={14} />}
                disabled={busy}
              >
                {state ? 'Replace' : 'Load reference'}
              </Button>
            )}
          </FileButton>
        </Group>
        {state?.applied && (
          <Text fz="0.72rem" c="teal.4">
            The world follows {state.applied.reference} ({state.applied.mode}).
          </Text>
        )}
        {state && (
          <>
            <SegmentedControl
              value={mode}
              onChange={(value) => setMode(value as AlignmentMode)}
              data={[
                { value: 'floor', label: 'Keep the floor' },
                { value: 'rigid', label: 'Rigid (6-DoF)' },
              ]}
            />
            {report == null ? (
              <Text fz="0.72rem" c="dark.3">
                No solve to compare yet.
              </Text>
            ) : (
              <>
                {report.refused && (
                  <Alert color="yellow" variant="light" p="xs">
                    <Text fz="0.72rem">{report.refused}</Text>
                  </Alert>
                )}
                <Table fz="0.72rem" verticalSpacing={2}>
                  <Table.Tbody>
                    {reportRows(report).map(([label, value]) => (
                      <Table.Tr key={label}>
                        <Table.Td c="dark.2">{label}</Table.Td>
                        <Table.Td className="rc-tnum">{value}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
                {Object.keys(report.residuals_m).length > 0 && (
                  <>
                    <Text fz="0.7rem" c="dark.3">
                      Residual per camera
                    </Text>
                    <Table fz="0.72rem" verticalSpacing={2}>
                      <Table.Tbody>
                        {residualRows(report).map(([name, value]) => (
                          <Table.Tr key={name}>
                            <Table.Td c="dark.2">{name}</Table.Td>
                            <Table.Td className="rc-tnum">{value}</Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </>
                )}
              </>
            )}
          </>
        )}
        {error && (
          <Text fz="0.72rem" c="var(--rc-error)">
            {error}
          </Text>
        )}
        <Group justify="space-between">
          <Button
            variant="subtle"
            color="gray"
            disabled={busy || !state}
            onClick={() =>
              void run(async () => {
                await deleteReference();
                show(null);
              })
            }
          >
            Remove reference
          </Button>
          <Button
            loading={busy}
            disabled={!report || report.refused != null}
            onClick={() =>
              void run(async () => {
                onResult(await orientExtrinsic({ op: 'align', mode }));
                show(await fetchReference());
              })
            }
          >
            Align
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
