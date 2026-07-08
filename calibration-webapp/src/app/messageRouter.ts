import {
  type CoverageMetrics,
  type Covisibility,
  coverageReceived,
  covisibilityReceived,
} from '@/features/telemetry/telemetrySlice';

// Centralized routing of LiveKit data-channel telemetry (spec realtime-telemetry).
// This module is the ONE boundary where a raw wire message is parsed and narrowed by
// its `type` before anything typed enters the store — screens never parse the channel.

// The slice actions this router can emit — one per telemetry `type`. Derived from the
// action creators so the union stays in sync if their payloads change.
type TelemetryAction =
  | ReturnType<typeof coverageReceived>
  | ReturnType<typeof covisibilityReceived>;

// Minimal boundary guard: an object carrying a string `type` discriminant. Once `type`
// matches a known case the rest of the shape is trusted from the service contract
// (spec realtime-telemetry) — we gate on the discriminant, not every field, as there
// is no runtime schema validator in the webapp.
function hasType(value: unknown): value is { type: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { type?: unknown }).type === 'string'
  );
}

// Parse + narrow a raw data-channel message and return the slice action to dispatch —
// or null for malformed JSON or an unknown `type` (both ignored, never throws). Adding
// a new telemetry type = one case here (+ its test), with zero screen changes.
export function routeDataChannelMessage(text: string): TelemetryAction | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null; // non-JSON payload — ignore
  }
  if (!hasType(data)) {
    return null;
  }
  switch (data.type) {
    case 'coverage_metrics':
      return coverageReceived(data as CoverageMetrics);
    case 'covisibility':
      return covisibilityReceived(data as Covisibility);
    default:
      return null; // unknown type — ignore
  }
}
