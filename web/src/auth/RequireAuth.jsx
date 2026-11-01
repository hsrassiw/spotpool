import { Navigate, Outlet, useLocation } from 'react-router';
import { Button, ButtonLink } from '../components/Button.jsx';
import Logo from '../components/Logo.jsx';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState.jsx';
import { useAuth } from './AuthContext.jsx';

export function RequireAuth() {
  const auth = useAuth();
  const location = useLocation();
  if (auth.status === 'checking') return <main className="access-page"><LoadingState message="Đang kiểm tra phiên truy cập…" /></main>;
  if (auth.status === 'error') return (
    <main className="access-page"><section className="panel session-problem">
      <Logo /><ErrorState message={auth.error} onRetry={() => auth.restore().catch(() => {})} />
      <Button variant="secondary" onClick={auth.signOut}>Nhập khóa khác</Button>
    </section></main>
  );
  if (auth.status !== 'authenticated') return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <Outlet />;
}

export function RequireAdmin() {
  const { profile } = useAuth();
  if (profile?.role !== 'admin') return (
    <section className="panel"><EmptyState title="Bạn không có quyền quản trị" description="Tài khoản hiện tại vẫn có thể sử dụng các chức năng người dùng." icon="alert">
      <ButtonLink to="/jobs" variant="secondary">Job của tôi</ButtonLink>
    </EmptyState></section>
  );
  return <Outlet />;
}
