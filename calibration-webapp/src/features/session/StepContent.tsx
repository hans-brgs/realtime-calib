import { TargetConfigScreen } from '@/features/board/TargetConfigScreen';
import { CameraSetupScreen } from '@/features/cameras/CameraSetupScreen';
import { ExportScreen } from '@/features/export/ExportScreen';
import { ExtrinsicScreen } from '@/features/extrinsic/ExtrinsicScreen';
import { IntrinsicsScreen } from '@/features/intrinsic/IntrinsicsScreen';
import { DashboardScreen } from '@/features/session/DashboardScreen';
import { LoadFromFilesScreen } from '@/features/session/LoadFromFilesScreen';
import type { NavTarget } from '@/features/session/selectors';

interface ScreenRouterProps {
  view: NavTarget;
  onNavigate: (id: NavTarget) => void;
}

// Maps the active view to its screen. Every wizard stage is live; the Load entry
// stays a gated screen until the replay/load-from-files pass (Phase 3.5).
export function StepContent({ view, onNavigate }: ScreenRouterProps) {
  switch (view) {
    case 'session':
      return <DashboardScreen onNavigate={onNavigate} />;
    case 'load':
      return <LoadFromFilesScreen onNavigate={onNavigate} />;
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
