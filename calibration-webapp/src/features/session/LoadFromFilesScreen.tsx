import { Box, Button, Group, Text, Title } from '@mantine/core';
import { IconArrowLeft, IconFolder } from '@tabler/icons-react';

import type { NavTarget } from '@/features/session/selectors';

interface LoadFromFilesScreenProps {
  onNavigate: (id: NavTarget) => void;
}

// Second entry mode (ADR-0019, spec replay-recalibration): open a session folder,
// derive the wizard state from its artifacts, and recompute/resume. The artifact
// inspection is backend work (Phase 3.5); until it lands this screen is an honest
// gated placeholder rather than a fixture-driven mock.
export function LoadFromFilesScreen({ onNavigate }: LoadFromFilesScreenProps) {
  return (
    <Box p={{ base: 'md', sm: 'xl' }} maw={860}>
      <Title order={1}>Load from files</Title>
      <Text c="dark.2" mt={9} maw={620} fz="0.9rem">
        Point to a session folder. We read its artifacts — recorded videos, board config,
        results — and derive what each wizard step can do, so you can recompute or resume where
        you left off.
      </Text>

      <Box
        mt="lg"
        p="lg"
        style={{
          border: '1px dashed var(--rc-border)',
          borderRadius: 'var(--mantine-radius-lg)',
          background: 'var(--rc-panel)',
        }}
      >
        <Group gap={12} wrap="nowrap" align="center">
          <Box
            style={{
              width: 42,
              height: 42,
              flex: 'none',
              borderRadius: 10,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'var(--rc-input)',
              border: '1px solid var(--rc-border)',
              color: 'var(--rc-text-subtle)',
            }}
          >
            <IconFolder size={22} stroke={1.8} />
          </Box>
          <Box style={{ minWidth: 0 }}>
            <Text ff="heading" fw={600} fz="0.95rem">
              Folder inspection is coming in Phase 3.5
            </Text>
            <Text c="dark.2" fz="0.82rem" mt={4} style={{ lineHeight: 1.55 }}>
              Reading a folder and deriving the wizard state depends on the recording &amp; replay
              backend, which is not wired yet. Start a realtime calibration for now — each intrinsic
              sweep is recorded, so it becomes loadable here later.
            </Text>
          </Box>
        </Group>
        <Group mt="lg" gap="sm">
          <Button disabled leftSection={<IconFolder size={16} />}>
            Choose folder…
          </Button>
        </Group>
      </Box>

      <Button
        mt="xl"
        variant="subtle"
        color="gray"
        leftSection={<IconArrowLeft size={16} />}
        onClick={() => onNavigate('session')}
      >
        Back to dashboard
      </Button>
    </Box>
  );
}
