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
        rig via PnP, pairwise stereo chaining and bundle adjustment.
      </>
    ),
  },
  {
    title: 'Caliscope-compatible',
    icon: '🔁',
    description: (
      <>
        Exports the same per-camera TOML fields as{' '}
        <a href="https://github.com/mprib/caliscope">Caliscope</a> plus aniposelib
        output — drop the results straight into your existing pipeline.
      </>
    ),
  },
  {
    title: 'Operator wizard',
    icon: '🧭',
    description: (
      <>
        A guided flow — cameras → board → intrinsics → extrinsics → 3D review →
        export — that works on desktop, tablet and mobile.
      </>
    ),
  },
  {
    title: 'Local & private',
    icon: '🔒',
    description: (
      <>
        Everything runs on your machine via Docker or <code>uv</code>. No cloud,
        no data leaving the rig — TLS and tablet access built in.
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
