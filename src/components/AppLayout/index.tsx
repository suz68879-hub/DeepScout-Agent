import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthContext';
import styles from './index.module.less';

const NAV_ITEMS = [
  { to: '/', label: '开始练习', end: true },
  { to: '/history', label: '历史记录' },
  { to: '/analytics', label: '数据分析' },
];

export default function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.brandIcon}>AI</span>
          <span>AI 面试陪练</span>
        </div>
        <nav className={styles.nav}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? styles.navActive : styles.navItem)}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className={styles.account}>
          <span>{user?.username}</span>
          <button type="button" onClick={() => void handleLogout()}>退出</button>
        </div>
      </header>
      <main className={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}
