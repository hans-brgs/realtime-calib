import { Alert, Box, Button, FileButton, Group, Text } from '@mantine/core';
import { IconFileImport } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import { InfoPopover } from '@/components/InfoPopover';
import {
  type SiteTemplateState,
  deleteSiteTemplate,
  errorMessage,
  fetchSiteTemplate,
  putSiteTemplate,
} from '@/transport/httpClient';

// The site template (ADR-0062): which camera goes to which port, where each one sits,
// and tape-measured distances. It is written by hand and deposited as a JSON file;
// loading or removing it applies at once, apart from the modal's Apply.
export function SiteTemplateSetting({ opened }: { opened: boolean }) {
  const [state, setState] = useState<SiteTemplateState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cleared after each pick, so choosing the same file again (corrected) re-uploads.
  const resetFile = useRef<() => void>(null);

  useEffect(() => {
    if (!opened) return;
    let alive = true;
    setError(null);
    fetchSiteTemplate()
      .then((next) => alive && setState(next))
      .catch((cause) => {
        if (!alive) return;
        setState(null);
        setError(errorMessage(cause, 'failed to load the template'));
      });
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
      setError(errorMessage(cause, 'template action failed'));
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
        throw new Error(`${file.name} is not JSON`);
      }
      setState(await putSiteTemplate(document));
    }).finally(() => resetFile.current?.());
  };

  const template = state?.template;
  return (
    <Box mb="md">
      <Group gap={6} wrap="nowrap" align="center" mb={6}>
        <Text fz="0.84rem" fw={500}>
          Site template
        </Text>
        <InfoPopover>
          The site&apos;s own checks before export: each camera at its expected port, inside its
          placement bounds, and the scale against tape-measured distances between cameras — the one
          check of the scale. Loading or removing it applies at once: Cancel does not undo it.
        </InfoPopover>
      </Group>
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Text fz="0.74rem" c={template ? undefined : 'dark.3'} truncate>
          {template
            ? `${template.name} · ${template.cameras.length} cameras · ${template.distances_m.length} distances · ${state.sha256.slice(0, 8)}`
            : 'None'}
        </Text>
        <Group gap={6} wrap="nowrap">
          {template && (
            <Button
              size="compact-sm"
              variant="subtle"
              color="gray"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await deleteSiteTemplate();
                  setState(null);
                })
              }
            >
              Remove
            </Button>
          )}
          <FileButton onChange={upload} accept="application/json,.json" resetRef={resetFile}>
            {(props) => (
              <Button
                {...props}
                size="compact-sm"
                variant="light"
                leftSection={<IconFileImport size={14} />}
                loading={busy}
              >
                {template ? 'Replace' : 'Load'}
              </Button>
            )}
          </FileButton>
        </Group>
      </Group>
      {error && (
        <Alert color="red" variant="light" p="xs" mt={6}>
          <Text fz="0.72rem">{error}</Text>
        </Alert>
      )}
    </Box>
  );
}
