import { Badge } from '@mantine/core';
import { IconPlayerRecordFilled } from '@tabler/icons-react';
import type { CSSProperties } from 'react';

// The "recording" pill overlaid on the capture preview — one component for the
// intrinsic and extrinsic screens, which carried byte-identical copies of it.
//
// Mantine's Badge provides the pill chrome and the left-section layout; the design's
// geometry and scrim are applied through the component's OWN CSS variables rather
// than raw background/padding, the same way DESTRUCTIVE_BUTTON_VARS does in theme.ts
// (Mantine merges the style prop after its vars resolver, so these win).
// Badge defaults to uppercase + bold, hence the explicit tt/fw: the label is cased copy.
export function RecordingBadge({ label, style }: { label: string; style?: CSSProperties }) {
  return (
    <Badge
      leftSection={<IconPlayerRecordFilled size={13} color="var(--rc-error)" />}
      tt="none"
      fw={600}
      radius={20}
      style={
        {
          position: 'absolute',
          '--badge-height': '26px',
          '--badge-padding-x': '10px',
          '--badge-fz': '0.72rem',
          '--badge-section-margin': '6px',
          '--badge-bg': 'rgba(9,9,11,0.7)',
          '--badge-color': 'var(--mantine-color-white)',
          ...style,
        } as CSSProperties
      }
    >
      {label}
    </Badge>
  );
}
