import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';

export default function RequireAuth() {
  const { loading, user } = useAuth();
  const location = useLocation();
  if (loading) return <div role="status">正在检查登录状态...</div>;
  if (!user) {
    return <Navigate to="/login" replace state={{ returnTo: location.pathname }} />;
  }
  return <Outlet />;
}
