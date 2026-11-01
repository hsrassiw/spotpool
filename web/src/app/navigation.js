// TV3 them trang trong App.jsx va dung chung layout, khong tao app thu hai.
export const navigation = [
  { to: '/jobs', label: 'Job của tôi', icon: 'jobs', end: true },
  { to: '/jobs/new', label: 'Tạo job', icon: 'plus' },
  { to: '/billing', label: 'Hóa đơn', icon: 'receipt' },
  { to: '/account', label: 'Tài khoản', icon: 'user' },
];

export function getPageTitle(pathname) {
  if (pathname === '/jobs/new') return 'Tạo job mới';
  if (pathname.startsWith('/jobs/')) return 'Chi tiết job';
  if (pathname === '/admin') return 'Tổng quan quản trị';
  return navigation.find((item) => item.to === pathname)?.label ?? 'Không tìm thấy trang';
}
