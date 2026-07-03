import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
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

function UserBadge({user}: {user: User}) {
  const logoUrl = useBaseUrl(user.logo ?? '');
  return (
    <Link className={styles.userBadge} href={user.url} title={user.name}>
      {user.logo ? (
        <img className={styles.userLogo} src={logoUrl} alt={user.name} />
      ) : (
        <span className={styles.userWordmark}>{user.name}</span>
      )}
    </Link>
  );
}

export default function UsedBy(): ReactNode {
  return (
    <section className={styles.usedBy}>
      <div className="container">
        <p className={styles.kicker}>Trusted in production by</p>
        <div className={styles.logos}>
          {USERS.map((user) => (
            <UserBadge key={user.name} user={user} />
          ))}
        </div>
      </div>
    </section>
  );
}
