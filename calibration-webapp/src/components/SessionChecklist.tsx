import { Box, Group, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconCircleCheckFilled } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import type { SessionIssue } from '@/transport/types';

// Self-checking checklist of the session's load-time anomalies (ADR-0036
// fail-loud), rendered as ONE Mantine notification (the toast chrome —
// positioning, stacking, transitions, close button — comes from the library).
//
// The backend REMOVES an issue as soon as the operator fixes it (reconfiguring
// the board purges its entry), so this component remembers every issue it has
// seen and diffs against the current list: a vanished issue renders CHECKED
// instead of disappearing. Once all are checked the toast turns green and
// auto-closes; it only returns if NEW anomalies appear.
//
// Side-effect-only component (renders null), mounted in the shell — same pattern
// as DataChannelListener.
interface SessionChecklistProps {
  issues: SessionIssue[];
}

const NOTIFICATION_ID = 'session-checklist';
const ALL_DONE_LINGER_MS = 4000;

const keyOf = (issue: SessionIssue): string => `${issue.step}:${issue.message}`;

function ChecklistBody({ seen, open }: { seen: SessionIssue[]; open: Set<string> }) {
  return (
    <>
      {seen.map((issue) => {
        const resolved = !open.has(keyOf(issue));
        return (
          <Group key={keyOf(issue)} gap={8} wrap="nowrap" align="flex-start" mb={4}>
            <Box style={{ flex: 'none', marginTop: 2 }}>
              {resolved ? (
                <IconCircleCheckFilled size={14} color="var(--rc-success)" />
              ) : (
                <IconAlertTriangle size={14} color="var(--rc-warning)" />
              )}
            </Box>
            <Text
              fz="0.78rem"
              c={resolved ? 'dark.3' : undefined}
              td={resolved ? 'line-through' : undefined}
              style={{ lineHeight: 1.45 }}
            >
              {issue.message}
            </Text>
          </Group>
        );
      })}
    </>
  );
}

export function SessionChecklist({ issues }: SessionChecklistProps) {
  const [seen, setSeen] = useState<SessionIssue[]>([]);
  const shown = useRef(false);

  // Remember every issue seen since the toast appeared (insertion order kept).
  useEffect(() => {
    const known = new Set(seen.map(keyOf));
    const fresh = issues.filter((issue) => !known.has(keyOf(issue)));
    if (fresh.length > 0) {
      setSeen((current) => [...current, ...fresh]);
    }
  }, [issues, seen]);

  useEffect(() => {
    if (seen.length === 0) {
      return;
    }
    const allResolved = issues.length === 0;
    const payload = {
      id: NOTIFICATION_ID,
      title: allResolved ? 'All fixed' : 'Session checklist',
      message: <ChecklistBody seen={seen} open={new Set(issues.map(keyOf))} />,
      color: allResolved ? 'green' : 'yellow',
      withCloseButton: true,
      autoClose: allResolved ? ALL_DONE_LINGER_MS : (false as const),
      // Fires on dismiss AND on auto-close: forget everything so a later
      // anomaly opens a fresh toast instead of updating a dead id.
      onClose: () => {
        shown.current = false;
        setSeen([]);
      },
    };
    if (shown.current) {
      notifications.update(payload);
    } else {
      notifications.show(payload);
      shown.current = true;
    }
  }, [issues, seen]);

  return null;
}
