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
        The service runs in Docker on the machine the cameras are plugged into — no
        desktop or GUI required on that host. Drive everything from a web app served
        over the local network, on <strong>any device</strong>: laptop, tablet or
        phone. Fits headless servers, robotics rigs, motion-capture setups and
        production lines.
      </>
    ),
  },
  {
    title: 'One-pass calibration',
    image: '/img/landing/one-pass.webp',
    alt: 'Live intrinsic capture with board detection and quality gauges',
    description: (
      <>
        No separate video-recording step: capture, detection, quality feedback and
        computation happen live, in a single flow.{' '}
        <strong>What you see is what gets calibrated.</strong>
      </>
    ),
  },
  {
    title: '3D review, backed by numbers',
    image: '/img/landing/review-3d.webp',
    alt: 'Extrinsic 3D review with camera frustums and per-camera reprojection error',
    description: (
      <>
        Inspect the solved multi-camera rig in 3D — every camera in one shared world
        frame — backed by objective figures: overall and per-camera reprojection
        error. Re-orient the world, re-run the solve, then export.
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
