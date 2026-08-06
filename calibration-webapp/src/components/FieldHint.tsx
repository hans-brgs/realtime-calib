import { ActionIcon, Group, Input, Popover, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { CSSProperties, ReactNode } from 'react';

// An info affordance next to a field label: tapping the icon opens a Popover with the
// help text, instead of an always-on `description` line spending vertical space under
// every field — a cost paid on every screen, by every operator, forever, and worst on
// the compact layouts (ADR-0041) where the screen already scrolls.
//
// Popover, not Tooltip: it opens on tap and dismisses on outside-tap, so it works on the
// touch devices the app targets (a hover Tooltip is dead weight on tablet/phone).
//
// What belongs behind the icon: the *why* — trade-offs, ranges, what a number does
// downstream. What stays inline as a `description`: anything the operator needs while
// looking at the field (a live count, a requirement, an error).
export function FieldHint({ children, about }: { children: ReactNode; about?: string }) {
  return (
    <Popover
      width={260}
      position="top-end"
      shadow="md"
      // Explicit opaque surface: the theme's default dropdown background is
      // see-through here, so the help text bled into the content behind it.
      // A raised surface (rc-input) also reads as "floating above" the panel.
      styles={{ dropdown: { background: 'var(--rc-input)', border: '1px solid var(--rc-border)' } }}
    >
      <Popover.Target>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          radius="xl"
          // Negative block margins: the tap target stays 26 px while the row it sits in
          // keeps the height of the label text alone, so adding a hint never shifts the
          // field down relative to a hint-less field beside it in a `Group grow`.
          my={-4}
          aria-label={about ? `About ${about}` : 'More information'}
        >
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

// A field's label with its hint behind the icon. Replaces the `label` prop of a Mantine
// input, which must then carry an explicit `id` matching `htmlFor` — `Input.Label` keeps
// Mantine's own label styling and the `for` wiring a bare <Text> would lose.
//
// The icon sits OUTSIDE the <label> on purpose: a labelable element nested in a <label>
// is invalid HTML, and a tap on it can activate the labelled control — popping the
// numeric keyboard on a NumberInput, or toggling a Switch.
//
// `labelStyle` takes the screen's label overrides (the wizard screens render smaller,
// dimmer labels than the Mantine default); the bottom margin is owned by the Group.
export function HintedLabel({
  htmlFor,
  hint,
  labelStyle,
  children,
}: {
  htmlFor: string;
  hint: ReactNode;
  labelStyle?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <Group gap={6} wrap="nowrap" align="center" mb={6}>
      <Input.Label htmlFor={htmlFor} style={{ ...labelStyle, marginBottom: 0 }}>
        {children}
      </Input.Label>
      <FieldHint about={typeof children === 'string' ? children : undefined}>{hint}</FieldHint>
    </Group>
  );
}
