import { ButtonLink } from '../components/Button.jsx';
import { EmptyState } from '../components/PageState.jsx';

export function NotFoundPage() {
  return (
    <section className="panel"><EmptyState title="Không tìm thấy trang" description="Đường dẫn này không có trong SpotPool. Bạn có thể quay lại danh sách job." icon="search">
      <ButtonLink to="/jobs" icon="arrow-left">Job của tôi</ButtonLink>
    </EmptyState></section>
  );
}
