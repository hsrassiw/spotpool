import { ButtonLink } from '../components/Button.jsx';
import Icon from '../components/Icon.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { EmptyState } from '../components/PageState.jsx';

export function CreateJobPage() {
  return (
    <>
      <PageHeader title="Tạo job mới" description="Chọn file, gói xử lý và trần chi phí."
        action={<ButtonLink to="/jobs" variant="secondary" icon="arrow-left">Quay lại</ButtonLink>} />
      <ol className="setup-steps" aria-label="Các bước tạo job">
        <li><span>1</span>Cấu hình</li><li><span>2</span>Tải file</li><li><span>3</span>Chạy</li>
      </ol>
      <div className="job-type-grid">
        <article className="panel job-type-card"><span className="state-icon"><Icon name="video" size={28} /></span><h2>Nén video</h2><p>Mỗi file video được xử lý thành một task riêng.</p></article>
        <article className="panel job-type-card"><span className="state-icon"><Icon name="code" size={28} /></span><h2>Chạy Python</h2><p>Tải file Python và nhận kết quả từ nội dung stdout.</p></article>
      </div>
      <section className="panel"><EmptyState title="Khu vực tạo job đang được chuẩn bị" description="Form chọn file, tải lên và xác nhận chạy sẽ được bổ sung ở bước tiếp theo." icon="plus" /></section>
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
