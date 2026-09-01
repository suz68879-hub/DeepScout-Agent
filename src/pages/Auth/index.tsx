import { FormEvent, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthContext';
import styles from './index.module.less';

interface LocationState {
  returnTo?: string;
}

export default function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { user, login, register } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const rawReturnTo = (location.state as LocationState | null)?.returnTo;
  const returnTo = rawReturnTo?.startsWith('/') && !rawReturnTo.startsWith('//') ? rawReturnTo : '/';

  if (user) return <Navigate to={returnTo} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      if (mode === 'login') await login(username, password);
      else await register(username, password, inviteCode);
      navigate(returnTo, { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '请求失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const isLogin = mode === 'login';
  return (
    <main className={styles.page}>
      <section className={styles.card} aria-labelledby="auth-title">
        <div className={styles.brand} aria-hidden="true">AI</div>
        <h1 id="auth-title">{isLogin ? '登录面试陪练' : '创建练习账号'}</h1>
        <p>{isLogin ? '继续你的面试练习与成长记录' : '账号数据相互隔离，仅你本人可见'}</p>
        <form onSubmit={submit} className={styles.form}>
          <label htmlFor="username">用户名</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            minLength={3}
            maxLength={32}
            pattern="[A-Za-z0-9_.\\-]+"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <span className={styles.hint}>3–32 位字母、数字、点、下划线或短横线</span>
          <label htmlFor="password">密码</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete={isLogin ? 'current-password' : 'new-password'}
            minLength={10}
            maxLength={128}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {!isLogin ? (
            <>
              <label htmlFor="invite-code">邀请码</label>
              <input
                id="invite-code"
                name="invite_code"
                autoComplete="off"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                maxLength={128}
                required
                value={inviteCode}
                onChange={(event) => setInviteCode(event.target.value)}
              />
              <span className={styles.hint}>由管理员下发，未持有邀请码无法注册</span>
            </>
          ) : null}
          {error ? <div className={styles.error} role="alert">{error}</div> : null}
          <button type="submit" disabled={submitting}>
            {submitting ? '请稍候...' : isLogin ? '登录' : '注册并登录'}
          </button>
        </form>
        <div className={styles.switch}>
          {isLogin ? '还没有账号？' : '已有账号？'}
          <Link to={isLogin ? '/register' : '/login'} state={{ returnTo }}>
            {isLogin ? '立即注册' : '返回登录'}
          </Link>
        </div>
      </section>
    </main>
  );
}
