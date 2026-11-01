import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router';
import { getPageTitle, navigation } from './navigation.js';
import Icon from '../components/Icon.jsx';
import Logo from '../components/Logo.jsx';
import { useAuth } from '../auth/AuthContext.jsx';
import { formatCredit } from '../utils/format.js';

function SidebarContent({ onNavigate, profile }) {
  const { pathname } = useLocation();
  const items = profile.role === 'admin'
    ? [...navigation, { to: '/admin', label: 'Tổng quan quản trị', icon: 'server' }]
    : navigation;
  return (
    <>
      <Link to="/jobs" className="brand-link" aria-label="SpotPool — Job của tôi" onClick={onNavigate}>
        <Logo />
      </Link>
      <nav className="sidebar-nav" aria-label="Điều hướng chính">
        {items.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} onClick={onNavigate}
            className={({ isActive }) => {
              const isJobDetail = item.to === '/jobs' && pathname.startsWith('/jobs/') && pathname !== '/jobs/new';
              return `nav-link${isActive || isJobDetail ? ' nav-link--active' : ''}`;
            }}>
            <Icon name={item.icon} size={21} />
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="workspace-label"><Icon name="grid" size={18} />Không gian làm việc</div>
        <span className="sidebar-caption">Xử lý gọn gàng, theo dõi dễ dàng.</span>
      </div>
    </>
  );
}

export default function AppLayout() {
  const { profile, signOut } = useAuth();
  const { pathname } = useLocation();
  const dialogRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const pageTitle = getPageTitle(pathname);

  useEffect(() => {
    document.title = `${pageTitle} · SpotPool`;
    window.scrollTo(0, 0);
  }, [pathname, pageTitle]);

  function openMenu() {
    dialogRef.current.showModal();
    setMenuOpen(true);
  }

  function closeMenu() {
    dialogRef.current.close();
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Bỏ qua điều hướng</a>
      <aside className="sidebar"><SidebarContent profile={profile} /></aside>
      <dialog ref={dialogRef} id="mobile-navigation" className="mobile-navigation"
        aria-label="Menu điều hướng" onClose={() => setMenuOpen(false)}
        onClick={(event) => { if (event.target === event.currentTarget) closeMenu(); }}>
        <div className="mobile-navigation-content">
          <button type="button" className="icon-button mobile-close" aria-label="Đóng menu" onClick={closeMenu}>
            <Icon name="close" />
          </button>
          <SidebarContent onNavigate={closeMenu} profile={profile} />
        </div>
      </dialog>

      <div className="app-workspace">
        <header className="topbar">
          <div className="topbar-start">
            <button type="button" className="icon-button mobile-menu" onClick={openMenu}
              aria-label="Mở menu" aria-controls="mobile-navigation" aria-expanded={menuOpen}>
              <Icon name="menu" />
            </button>
            <nav className="breadcrumb" aria-label="Đường dẫn trang">
              <Link to="/jobs" aria-label="Job của tôi"><Icon name="home" size={18} /></Link>
              <Icon name="chevron" size={15} />
              <span aria-current="page">{pageTitle}</span>
            </nav>
          </div>
          <div className="topbar-account">
            <span className="credit-chip"><Icon name="coins" size={18} />{formatCredit(profile.creditAvailable)} credit</span>
            <Link to="/account" className="account-link" aria-label={`Tài khoản ${profile.email || profile.userId}`}>
              <span className="avatar" aria-hidden="true">{(profile.email || profile.userId).slice(0, 1).toUpperCase()}</span>
              <span className="account-email">{profile.email || profile.userId.slice(0, 8)}</span>
              <Icon name="chevron-down" size={15} />
            </Link>
            <button type="button" className="sign-out-button" onClick={signOut}>Thoát</button>
          </div>
        </header>

        <main id="main-content" className="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="app-footer"><span>SpotPool</span><span>Quản lý tác vụ xử lý trên đám mây</span></footer>
      </div>
    </div>
  );
}
