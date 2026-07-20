import { TargetConfigScreen } from '@/features/board/TargetConfigScreen';
import { CameraSetupScreen } from '@/features/cameras/CameraSetupScreen';
import { ExportScreen } from '@/features/export/ExportScreen';
import { ExtrinsicScreen } from '@/features/extrinsic/ExtrinsicScreen';
import { IntrinsicsScreen } from '@/features/intrinsic/IntrinsicsScreen';
import { DashboardScreen } from '@/features/session/DashboardScreen';
import type { NavTarget } from '@/features/session/selectors';

interface ScreenRouterProps {
  view: NavTarget;
}

// Maps the active view to its screen. Every wizard stage is live; loading from
// files is a dashboard modal (ADR-0035), not a view of its own.
export function StepContent({ view }: ScreenRouterProps) {
  switch (view) {
    case 'session':
      return <DashboardScreen />;
    case 'boards':
      return <TargetConfigScreen />;
    case 'cameras':
      return <CameraSetupScreen />;
    case 'intrinsic':
      return <IntrinsicsScreen />;
    case 'extrinsic':
      return <ExtrinsicScreen />;
    case 'export':
      return <ExportScreen />;
    default:
      return null;
  }
}
