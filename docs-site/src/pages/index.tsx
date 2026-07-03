import type {ReactNode} from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import UsedBy from '@site/src/components/UsedBy';
import Heading from '@theme/Heading';

import styles from './index.module.css';

function HomepageHero() {
  const {siteConfig} = useDocusaurusContext();
  return (
    <header className={clsx('hero', styles.hero)}>
      <div className="container">
        <span className={styles.eyebrow}>Open source · AGPL-3.0</span>
        <Heading as="h1" className={styles.heroTitle}>
          {siteConfig.title}
        </Heading>
        <p className={styles.heroTagline}>
          Real-time multi-camera calibration — intrinsics, distortion and 6-DoF
          extrinsics for a rig of USB cameras, with live feedback and{' '}
          <strong>Caliscope-compatible</strong> exports.
        </p>
        <div className={styles.heroButtons}>
          <Link className="button button--primary button--lg" to="/docs/intro">
            Get started
          </Link>
          <Link
            className="button button--secondary button--lg"
            to="/docs/guides/configure-cameras">
            Read the guides
          </Link>
          <Link
            className={clsx('button button--outline button--lg', styles.ghostBtn)}
            href="https://github.com/hans-brgs/realtime-calib">
            ★ Star on GitHub
          </Link>
        </div>
        <p className={styles.heroNote}>
          Runs locally · Docker or <code>uv</code> · desktop, tablet &amp; mobile
          operator UI
        </p>
      </div>
    </header>
  );
}

function SupportCallout() {
  return (
    <section className={styles.support}>
      <div className="container">
        <div className={styles.supportCard}>
          <Heading as="h2" className={styles.supportTitle}>
            Support the project
          </Heading>
          <p className={styles.supportText}>
            realtime-calib is free and open source under AGPL-3.0. If it saves you
            time, you can fuel its development.
          </p>
          <div className={styles.supportButtons}>
            <Link
              className="button button--primary"
              href="https://github.com/sponsors/hans-brgs">
              ♥ Sponsor on GitHub
            </Link>
            <Link
              className="button button--secondary"
              href="https://ko-fi.com/myosin">
              ☕ Buy me a token
            </Link>
          </div>
          <p className={styles.supportFinePrint}>
            Need it for a proprietary or commercial product?{' '}
            <Link to="/docs/contributing/license#commercial-use">
              A commercial license &amp; custom development
            </Link>{' '}
            are available.
          </p>
        </div>
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  const {siteConfig} = useDocusaurusContext();
  return (
    <Layout
      title={`${siteConfig.title} — real-time multi-camera calibration`}
      description="Real-time multi-camera intrinsic and extrinsic calibration with live feedback and Caliscope-compatible exports.">
      <HomepageHero />
      <main>
        <HomepageFeatures />
        <UsedBy />
        <SupportCallout />
      </main>
    </Layout>
  );
}
