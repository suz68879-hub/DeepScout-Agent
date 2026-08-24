import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { ApiError, getJson, postJson } from '@/api/rest';

export interface AuthUser {
  id: string;
  username: string;
}

interface AuthContextValue {
  loading: boolean;
  user: AuthUser | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let active = true;
    getJson<AuthUser>('/api/auth/me')
      .then((current) => {
        if (active) setUser(current);
      })
      .catch((error: unknown) => {
        if (active && (!(error instanceof ApiError) || error.status !== 401)) {
          setUser(null);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    const handleUnauthorized = () => setUser(null);
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => {
      active = false;
      window.removeEventListener('auth:unauthorized', handleUnauthorized);
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setUser(await postJson<AuthUser>('/api/auth/login', { username, password }));
  }, []);

  const register = useCallback(async (username: string, password: string) => {
    setUser(await postJson<AuthUser>('/api/auth/register', { username, password }));
  }, []);

  const logout = useCallback(async () => {
    await postJson<void>('/api/auth/logout', {});
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ loading, user, login, register, logout }),
    [loading, user, login, register, logout]
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
