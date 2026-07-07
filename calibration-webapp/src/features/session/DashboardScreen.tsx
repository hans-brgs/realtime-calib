import { Box, Group, SimpleGrid, Table, Text, Title, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconChevronRight,
  IconFolder,
  IconVideo,
  type IconProps,
} from '@tabler/icons-react';
import { type ComponentType, useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { NewSessionModal } from '@/features/session/NewSessionModal';
import {
  fetchRecentSessions,
  openSessionThunk,
  selectRecentSessions,
} from '@/features/session/sessionSlice';
import type { NavTarget } from '@/features/session/selectors';
import type { SessionSummary } from '@/transport/types';

interface EntryCard {
  icon: ComponentType<IconProps>;
  title: string;
  subtitle: string;
  desc: string;
  cta: string;
  accent: boolean;
  onClick?: () => void;
}

function EntryCardTile({ card }: { card: EntryCard }) {
  const Icon = card.icon;
  return (
    <UnstyledButton
      onClick={card.onClick}
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: 208,
        padding: 20,
        borderRadius: 'var(--mantine-radius-xl)',
        border: `1px solid ${card.accent ? 'rgba(167,139,250,0.3)' : 'var(--mantine-color-dark-4)'}`,
        background: card.accent
          ? 'linear-gradient(180deg, rgba(139,92,246,0.12), #0f0f12)'
          : 'linear-gradient(180deg, #131317, #0f0f12)',
      }}
    >
      <Box
        style={{
          width: 46,
          height: 46,
          borderRadius: 11,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: card.accent ? 'rgba(167,139,250,0.12)' : 'var(--rc-input)',
          border: `1px solid ${card.accent ? 'rgba(167,139,250,0.3)' : 'var(--mantine-color-dark-4)'}`,
          color: card.accent ? 'var(--rc-accent-bright)' : 'var(--mantine-color-dark-2)',
        }}
      >
        <Icon size={22} stroke={1.8} />
      </Box>
      <Text ff="heading" fw={600} fz="1rem" mt={16}>
        {card.title}
      </Text>
      <Text
        fz="0.72rem"
        mt={5}
        c={card.accent ? 'var(--rc-accent)' : 'var(--rc-text-subtle)'}
        style={{ letterSpacing: '0.01em' }}
      >
        {card.subtitle}
      </Text>
      <Text c="dark.2" fz="0.81rem" mt={10} style={{ lineHeight: 1.55 }}>
        {card.desc}
      </Text>
      <Group
        gap={6}
        mt="auto"
        pt={16}
        wrap="nowrap"
        style={{ color: card.accent ? 'var(--rc-accent)' : 'var(--mantine-color-dark-2)' }}
      >
        <Text ff="heading" fw={600} fz="0.75rem" style={{ letterSpacing: '0.02em' }} inherit>
          {card.cta}
        </Text>
        <IconChevronRight size={14} />
      </Group>
    </UnstyledButton>
  );
}

const STATUS_CHIP: Record<SessionSummary['status'], { label: string; fg: string; bg: string }> = {
  complete: { label: 'complete', fg: 'var(--rc-success)', bg: 'rgba(52,211,153,0.1)' },
  in_progress: { label: 'in progress', fg: 'var(--rc-accent-bright)', bg: 'rgba(167,139,250,0.12)' },
  empty: { label: 'empty', fg: 'var(--mantine-color-dark-2)', bg: '#1a1a20' },
};

// Recent-sessions columns; the last one is right-aligned (the open chevron).
const COLUMNS: { label: string; align: 'left' | 'right' }[] = [
  { label: 'Session ID', align: 'left' },
  { label: 'Date', align: 'left' },
  { label: 'Cameras', align: 'left' },
  { label: 'Status', align: 'left' },
  { label: 'Open', align: 'right' },
];

const TH_STYLE = {
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  fontSize: '0.66rem',
  fontWeight: 600,
  color: 'var(--mantine-color-dark-3)',
} as const;

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function StatusChip({ status }: { status: SessionSummary['status'] }) {
  const chip = STATUS_CHIP[status];
  return (
    <Group
      gap={6}
      wrap="nowrap"
      display="inline-flex"
      style={{
        alignItems: 'center',
        padding: '3px 10px 3px 8px',
        borderRadius: 20,
        background: chip.bg,
      }}
    >
      <Box w={6} h={6} style={{ borderRadius: '50%', background: chip.fg }} />
      <Text fz="0.72rem" style={{ color: chip.fg }}>
        {chip.label}
      </Text>
    </Group>
  );
}

interface DashboardScreenProps {
  onNavigate: (id: NavTarget) => void;
}

// Session entry (dashboard): the two FSM entry modes (ADR-0019) as workflow cards +
// recent sessions. The sessions list endpoint is not wired yet, so the table shows an
// honest empty state rather than fixtures.
export function DashboardScreen({ onNavigate }: DashboardScreenProps) {
  const dispatch = useAppDispatch();
  const recent = useAppSelector(selectRecentSessions);
  const [newOpened, { open: openNew, close: closeNew }] = useDisclosure(false);
  const [openError, setOpenError] = useState<string | null>(null);

  useEffect(() => {
    dispatch(fetchRecentSessions());
  }, [dispatch]);

  // Switch the active session server-side (ADR-0028); the wizard rail then follows
  // the persisted step and navigates to where this session was left off. On failure
  // (e.g. the folder was deleted on disk) surface it and refresh the list.
  const openSession = (session: SessionSummary) => {
    setOpenError(null);
    dispatch(openSessionThunk(session.session_id))
      .unwrap()
      .catch((err: unknown) => {
        setOpenError(err instanceof Error ? err.message : 'could not open the session');
        void dispatch(fetchRecentSessions());
      });
  };

  const cards: EntryCard[] = [
    {
      icon: IconVideo,
      title: 'New realtime calibration',
      subtitle: 'Live capture · records video as you go',
      desc: 'Run the full wizard from scratch. Each intrinsic sweep is recorded to the session folder, so it can be replayed and recomputed later.',
      cta: 'START WIZARD',
      accent: true,
      onClick: openNew,
    },
    {
      icon: IconFolder,
      title: 'Load from files',
      subtitle: 'Open a session folder · replay & resume',
      desc: 'Pick a folder and see which artifacts are present — videos, board, results — and the derived wizard state. Recompute or resume where you left off.',
      cta: 'CHOOSE FOLDER',
      accent: false,
      onClick: () => onNavigate('load'),
    },
  ];

  return (
    <Box p={{ base: 'md', sm: 'xl' }} maw={1180}>
      <NewSessionModal opened={newOpened} onClose={closeNew} />
      <Title order={1}>Welcome to the calibration bench</Title>
      <Text c="dark.2" mt={9} maw={600} fz="0.9rem">
        Prepare and validate your optical systems with sub-millimetric precision. Choose a workflow
        to begin — or resume a recent session.
      </Text>

      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md" mt="lg">
        {cards.map((card) => (
          <EntryCardTile key={card.title} card={card} />
        ))}
      </SimpleGrid>

      <Group justify="space-between" align="baseline" mt={34}>
        <Title order={3}>Recent sessions</Title>
        {openError && (
          <Text fz="0.78rem" c="var(--rc-error)">
            {openError}
          </Text>
        )}
      </Group>
      <Box
        mt="sm"
        style={{
          border: '1px solid var(--mantine-color-dark-4)',
          borderRadius: 'var(--mantine-radius-lg)',
          background: 'var(--rc-panel)',
          overflow: 'hidden',
        }}
      >
        <Table.ScrollContainer minWidth={520}>
          <Table highlightOnHover horizontalSpacing="md" verticalSpacing="sm">
            <Table.Thead style={{ background: '#121216' }}>
              <Table.Tr>
                {COLUMNS.map((col) => (
                  <Table.Th key={col.label} ta={col.align} style={TH_STYLE}>
                    {col.label}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {recent.length > 0 ? (
                recent.map((session) => (
                  <Table.Tr
                    key={session.session_id}
                    onClick={() => openSession(session)}
                    style={{ cursor: 'pointer' }}
                  >
                    <Table.Td fz="0.81rem" fw={600} c="var(--rc-accent)" className="rc-tnum">
                      {session.session_id}
                    </Table.Td>
                    <Table.Td fz="0.81rem" c="dark.2" className="rc-tnum">
                      {formatDate(session.modified_at)}
                    </Table.Td>
                    <Table.Td fz="0.81rem" className="rc-tnum">
                      {session.camera_count}
                    </Table.Td>
                    <Table.Td>
                      <StatusChip status={session.status} />
                    </Table.Td>
                    <Table.Td ta="right">
                      <IconChevronRight size={15} color="var(--mantine-color-dark-3)" />
                    </Table.Td>
                  </Table.Tr>
                ))
              ) : (
                <Table.Tr>
                  <Table.Td colSpan={COLUMNS.length}>
                    <Box py={30} ta="center">
                      <Text c="dark.3" fz="0.84rem">
                        No recorded sessions yet — start a new calibration above.
                      </Text>
                    </Box>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Box>

      <Text mt="lg" fz="0.72rem" c="dark.3" className="rc-tnum" style={{ letterSpacing: '0.04em' }}>
        ENGINE STATUS: <Text span c="var(--rc-success)" inherit>OPTIMIZED</Text> · CPU-ONLY · LIVEKIT
      </Text>
    </Box>
  );
}
