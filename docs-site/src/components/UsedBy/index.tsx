import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Heading from '@theme/Heading';
import styles from './styles.module.css';

type User = {
  name: string;
  url: string;
  /** Path under /static, e.g. 'img/users/inmersiv.png'. Falls back to a wordmark. */
  logo?: string;
};

// Social proof — organizations using realtime-calib in production.
// To add a logo: drop the file in static/img/users/ and set `logo` below.
const USERS: User[] = [
  {
    name: 'Inmersiv',
    url: 'https://inmersiv.com',
    logo: 'img/users/inmersiv.png',
  },
];

function UserCell({user}: {user: User}) {
  const logoUrl = useBaseUrl(user.logo ?? '');
  return (
    <Link className={styles.cell} href={user.url} title={user.name}>
      {user.logo ? (
        <img className={styles.logo} src={logoUrl} alt={user.name} />
      ) : (
        <span className={styles.wordmark}>{user.name}</span>
      )}
      <span className={styles.name}>{user.name}</span>
    </Link>
  );
}

export default function UsedBy(): ReactNode {
  return (
    <section className={styles.usedBy}>
      <div className="container">
        <Heading as="h2" className={styles.title}>
          Already trusted in production
        </Heading>
        <p className={styles.subtitle}>
          Powering real multi-camera calibration setups — not a lab demo.
        </p>
        <div className={styles.grid}>
          {USERS.map((user) => (
            <UserCell key={user.name} user={user} />
          ))}
        </div>
      </div>
    </section>
  );
}
