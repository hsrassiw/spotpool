import { useParams } from 'react-router';
import { ButtonLink } from '../components/Button.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { EmptyState } from '../components/PageState.jsx';

export default function JobDetailPage() {
  const { jobId } = useParams();
  return (
    <>
      <PageHeader title="Chi tiết job" description={`Mã job: ${jobId}`}
        action={<ButtonLink to="/jobs" variant="secondary" icon="arrow-left">Job của tôi</ButtonLink>} />
      <section className="panel"><EmptyState title="Trang chi tiết đang được xây dựng"
        description="Tiến độ từng task, log và kết quả tải về sẽ có ở bước 5. Bạn có thể xem trạng thái hiện tại trong danh sách job." icon="jobs" /></section>
    </>
  );
}
