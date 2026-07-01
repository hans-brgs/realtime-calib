import { Box, Group, SimpleGrid, Text, Title, UnstyledButton } from '@mantine/core';
import {
  IconChevronRight,
  IconCirclePlus,
  IconFileImport,
  IconSquareCheck,
  type IconProps,
} from '@tabler/icons-react';
import { type ComponentType, useEffect } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { fetchRecentSessions, selectRecentSessions } from '@/features/session/sessionSlice';
import { stepToView, type ViewId } from '@/features/session/selectors';
import type { SessionSummary } from '@/transport/types';

interface EntryCard {
  icon: ComponentType<IconProps>;
  title: string;
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
        border: '1px solid var(--mantine-color-dark-4)',
        background: 'linear-gradient(180deg, #131317, #0f0f12)',
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
      <Text c="dark.2" fz="0.81rem" mt={8} style={{ lineHeight: 1.55 }}>
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

const SESSION_GRID = '1.5fr 1.6fr 0.8fr 1fr 0.5fr';

const STATUS_CHIP: Record<SessionSummary['status'], { label: string; fg: string; bg: string }> = {
  complete: { label: 'complete', fg: 'var(--rc-success)', bg: 'rgba(52,211,153,0.1)' },
  in_progress: { label: 'in progress', fg: 'var(--rc-accent-bright)', bg: 'rgba(167,139,250,0.12)' },
  empty: { label: 'empty', fg: 'var(--mantine-color-dark-2)', bg: '#1a1a20' },
};

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

function SessionRow({
  session,
  onOpen,
}: {
  session: SessionSummary;
  onOpen: (session: SessionSummary) => void;
}) {
  const chip = STATUS_CHIP[session.status];
  return (
    <UnstyledButton
      onClick={() => onOpen(session)}
      style={{
        display: 'grid',
        gridTemplateColumns: SESSION_GRID,
        gap: 12,
        padding: '15px 18px',
        borderTop: '1px solid var(--mantine-color-dark-4)',
        alignItems: 'center',
        textAlign: 'left',
      }}
    >
      <Text fz="0.81rem" fw={600} c="var(--rc-accent)" className="rc-tnum" truncate>
        {session.session_id}
      </Text>
      <Text fz="0.81rem" c="dark.2" className="rc-tnum">
        {formatDate(session.modified_at)}
      </Text>
      <Text fz="0.81rem" className="rc-tnum">
        {session.camera_count}
      </Text>
      <Box>
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
      </Box>
      <Box ta="right">
        <IconChevronRight size={15} color="var(--mantine-color-dark-3)" />
      </Box>
    </UnstyledButton>
  );
}

interface DashboardScreenProps {
  onNavigate: (id: ViewId) => void;
}

// Session entry (dashboard): the three FSM entry modes as workflow cards + recent
// sessions. The sessions list endpoint is not wired yet (Phase 1), so the table
// shows an honest empty state rather than fixtures.
export function DashboardScreen({ onNavigate }: DashboardScreenProps) {
  const dispatch = useAppDispatch();
  const recent = useAppSelector(selectRecentSessions);

  useEffect(() => {
    dispatch(fetchRecentSessions());
  }, [dispatch]);

  const openSession = (session: SessionSummary) => onNavigate(stepToView(session.step));

  const cards: EntryCard[] = [
    {
      icon: IconCirclePlus,
      title: 'New Calibration',
      desc: 'Run the full intrinsic + extrinsic workflow from scratch.',
      cta: 'START WIZARD',
      accent: true,
      onClick: () => onNavigate('cameras'),
    },
    {
      icon: IconFileImport,
      title: 'Load Intrinsics',
      desc: 'Reuse existing optics to skip straight to spatial alignment.',
      cta: 'IMPORT .toml',
      accent: false,
    },
    {
      icon: IconSquareCheck,
      title: 'Load Full Calibration',
      desc: 'Import a complete calibration to inspect metrics and the 3D model.',
      cta: 'IMPORT .zip',
      accent: false,
    },
  ];

  return (
    <Box p={{ base: 'md', sm: 'xl' }} maw={1180}>
      <Title order={1}>Welcome to the calibration bench</Title>
      <Text c="dark.2" mt={9} maw={600} fz="0.9rem">
        Prepare and validate your camera array with sub-millimetric precision. Choose a workflow to
        begin — or resume a recent session.
      </Text>

      <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }} spacing="md" mt="lg">
        {cards.map((card) => (
          <EntryCardTile key={card.title} card={card} />
        ))}
      </SimpleGrid>

      <Group justify="space-between" align="baseline" mt={34}>
        <Title order={3}>Recent sessions</Title>
      </Group>
      <Box
        mt="sm"
        style={{
          border: '1px solid var(--mantine-color-dark-4)',
          borderRadius: 'var(--mantine-radius-lg)',
          background: 'var(--rc-panel)',
        }}
      >
        <Box
          py={11}
          px="md"
          style={{
            display: 'grid',
            gridTemplateColumns: SESSION_GRID,
            gap: 12,
            background: '#121216',
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
          }}
        >
          {['Session ID', 'Date', 'Cameras', 'Status', 'Open'].map((h, i) => (
            <Text key={h} fz="0.66rem" fw={600} c="dark.3" ta={i === 4 ? 'right' : 'left'}>
              {h}
            </Text>
          ))}
        </Box>
        {recent.length > 0 ? (
          recent.map((session) => (
            <SessionRow key={session.session_id} session={session} onOpen={openSession} />
          ))
        ) : (
          <Box py={40} ta="center">
            <Text c="dark.3" fz="0.84rem">
              No recorded sessions yet — start a new calibration above.
            </Text>
          </Box>
        )}
      </Box>

      <Text mt="lg" fz="0.72rem" c="dark.3" className="rc-tnum" style={{ letterSpacing: '0.04em' }}>
        ENGINE STATUS: <Text span c="var(--rc-success)" inherit>OPTIMIZED</Text> · CPU-ONLY · LIVEKIT
      </Text>
    </Box>
  );
}
