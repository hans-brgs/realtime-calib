import { ActionIcon, Popover, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { ReactNode } from 'react';

// The app's one "explain this" affordance: an info icon that opens a Popover with
// help text on demand, instead of an always-on description line under every field.
//
// Popover, not Tooltip — it opens on tap and dismisses on outside-tap, so it works on
// the touch devices the app targets (a hover Tooltip is dead weight on tablet/phone).
//
// Where it sits matters: next to a form control, keep the icon OUTSIDE the field's
// <label> — nested inside, a tap would toggle a Switch or pop the numeric keyboard on
// a NumberInput through the label's implicit control activation.
export function InfoPopover({
  children,
  label = 'More information',
  width = 260,
  position = 'top-end',
}: {
  children: ReactNode;
  // Accessible name of the icon button — override when several sit on one screen and
  // "More information" alone would not say which metric is being explained.
  label?: string;
  width?: number;
  position?: 'top' | 'top-end' | 'top-start' | 'bottom' | 'bottom-end' | 'bottom-start';
}) {
  return (
    <Popover
      width={width}
      position={position}
      shadow="md"
      // Explicit opaque surface: the theme's default dropdown background is
      // see-through here, so the help text bled into the content behind it.
      // A raised surface (rc-input) also reads as "floating above".
      styles={{ dropdown: { background: 'var(--rc-input)', border: '1px solid var(--rc-border)' } }}
    >
      <Popover.Target>
        <ActionIcon variant="subtle" color="gray" size="sm" radius="xl" aria-label={label}>
          <IconInfoCircle size={16} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Text fz="0.72rem" c="dark.1" style={{ lineHeight: 1.5 }}>
          {children}
        </Text>
      </Popover.Dropdown>
    </Popover>
  );
}
