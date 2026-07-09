import type {ReactNode} from 'react';
import clsx from 'clsx';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  icon: string;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Real-time feedback',
    icon: '⚡',
    description: (
      <>
        Camera streams and quality overlays flow live over LiveKit. See coverage,
        reprojection error and board detection as you move — no capture-then-wait
        loop.
      </>
    ),
  },
  {
    title: 'Intrinsics & extrinsics',
    icon: '🎯',
    description: (
      <>
        Per-camera focal length and distortion, then full 6-DoF pose across the
        rig via pairwise stereo calibration, anchor chaining and bundle adjustment.
      </>
    ),
  },
  {
    title: 'Caliscope-compatible',
    icon: '🔁',
    description: (
      <>
        Exports the same per-camera TOML fields as{' '}
        <a href="https://github.com/mprib/caliscope">Caliscope</a>, plus engine-ready
        JSON (three.js, Blender, Unity, Unreal) — drop the results straight into
        your pipeline.
      </>
    ),
  },
  {
    title: 'Operator wizard',
    icon: '🧭',
    description: (
      <>
        A guided flow — board → cameras → intrinsics → extrinsics (with 3D review)
        → export — that runs in the browser on desktop, tablet or mobile.
      </>
    ),
  },
  {
    title: 'Local & private',
    icon: '🔒',
    description: (
      <>
        Everything runs on the machine your cameras are plugged into — even a
        headless server — and you drive it from any device on your LAN. No cloud,
        no data leaving the rig; TLS and tablet access built in.
      </>
    ),
  },
  {
    title: 'Open source',
    icon: '📖',
    description: (
      <>
        AGPL-3.0, developed in the open. A commercial license and custom
        development are available for proprietary use.
      </>
    ),
  },
];

function Feature({title, icon, description}: FeatureItem) {
  return (
    <div className={clsx('col col--4', styles.featureCol)}>
      <div className={styles.featureCard}>
        <div className={styles.featureIcon} aria-hidden="true">
          {icon}
        </div>
        <Heading as="h3" className={styles.featureTitle}>
          {title}
        </Heading>
        <p className={styles.featureText}>{description}</p>
      </div>
    </div>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <div className="row">
          {FeatureList.map((props, idx) => (
            <Feature key={idx} {...props} />
          ))}
        </div>
      </div>
    </section>
  );
}
