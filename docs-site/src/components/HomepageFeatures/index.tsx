import type {ReactNode} from 'react';
import clsx from 'clsx';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type FeatureItem = {
  title: string;
  image: string;
  alt: string;
  description: ReactNode;
};

const FeatureList: FeatureItem[] = [
  {
    title: 'Runs headless, driven from any device',
    image: '/img/landing/devices.webp',
    alt: 'realtime-calib running on a phone, a tablet and a laptop',
    description: (
      <>
        Cameras plug into a headless server; drive the whole calibration from a
        browser on <strong>any device</strong> — laptop, tablet or phone. Built for
        robotics, mocap and production lines.
      </>
    ),
  },
  {
    title: 'One-pass calibration',
    image: '/img/landing/one-pass.webp',
    alt: 'Live intrinsic capture with board detection and quality gauges',
    description: (
      <>
        Capture, live feedback and compute in a single flow — no separate recording
        step. <strong>What you see is what gets calibrated.</strong>
      </>
    ),
  },
  {
    title: '3D review, backed by numbers',
    image: '/img/landing/review-3d.webp',
    alt: 'Extrinsic 3D review with camera frustums and per-camera reprojection error',
    description: (
      <>
        Inspect the solved rig in 3D, backed by hard numbers — overall and
        per-camera reprojection error — then export.
      </>
    ),
  },
];

function Feature({title, image, alt, description}: FeatureItem) {
  const imageUrl = useBaseUrl(image);
  return (
    <div className={clsx('col col--4', styles.featureCol)}>
      <div className={styles.featureCard}>
        <div className={styles.featureMedia}>
          <img
            src={imageUrl}
            alt={alt}
            loading="lazy"
            className={styles.featureImg}
          />
        </div>
        <div className={styles.featureBody}>
          <Heading as="h3" className={styles.featureTitle}>
            {title}
          </Heading>
          <p className={styles.featureText}>{description}</p>
        </div>
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
