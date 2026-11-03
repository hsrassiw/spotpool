import { ButtonLink } from '../components/Button.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { EmptyState } from '../components/PageState.jsx';

export function BillingPage() {
  return (
    <>
      <PageHeader title="Credit và hóa đơn" description="Theo dõi số dư và chi phí của từng job." />
      <section className="panel"><EmptyState title="Hóa đơn sẽ xuất hiện tại đây" description="Chức năng nạp credit và lịch sử quyết toán đang được chuẩn bị." icon="receipt">
        <ButtonLink to="/jobs" variant="secondary">Xem job của tôi</ButtonLink>
      </EmptyState></section>
    </>
  );
}

export function AdminPage() {
  return (
    <>
      <PageHeader title="Tổng quan quản trị" description="Tài nguyên xử lý và chi phí vận hành." eyebrow="Bản xem trước" />
      <section className="panel"><EmptyState title="Không gian quản trị" description="Thông tin máy, hàng đợi, doanh thu và chi phí sẽ được hiển thị sau khi tích hợp." icon="server" /></section>
    </>
  );
}

export function NotFoundPage() {
  return (
    <section className="panel"><EmptyState title="Không tìm thấy trang" description="Đường dẫn này không có trong SpotPool. Bạn có thể quay lại danh sách job." icon="search">
      <ButtonLink to="/jobs" icon="arrow-left">Job của tôi</ButtonLink>
    </EmptyState></section>
  );
}
