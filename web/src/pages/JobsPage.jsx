import { useState } from 'react';
import { Link } from 'react-router';
import { Button, ButtonLink } from '../components/Button.jsx';
import Icon from '../components/Icon.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState.jsx';
import ProgressBar from '../components/ProgressBar.jsx';
import StatCard from '../components/StatCard.jsx';
import StatusBadge, { statusOptions } from '../components/StatusBadge.jsx';
import { useJobs } from '../jobs/useJobs.js';
import { useAuth } from '../auth/AuthContext.jsx';
import { formatCredit, formatDate, kindLabel, tierLabel } from '../utils/format.js';

const quickFilters = [
  { value: '', label: 'Tất cả' },
  { value: 'RUNNING', label: 'Đang chạy' },
  { value: 'SUCCEEDED', label: 'Hoàn thành' },
  { value: 'PAUSED', label: 'Tạm dừng' },
];

export default function JobsPage() {
  const { profile } = useAuth();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const list = useJobs(status);
  const query = search.trim().toLocaleLowerCase('vi');
  const jobs = list.jobs.filter((job) => job.name.toLocaleLowerCase('vi').includes(query));

  function resetFilters() {
    setSearch('');
    setStatus('');
  }

  return (
    <>
      <PageHeader title="Job của tôi" description="Theo dõi tiến độ và tải kết quả xử lý."
        action={<ButtonLink to="/jobs/new" icon="plus">Tạo job</ButtonLink>} />

      <div className="stats-grid">
        <StatCard title="Credit khả dụng" value={formatCredit(profile.creditAvailable)} icon="coins">
          Đang giữ: {formatCredit(profile.creditHeld)} credit
        </StatCard>
        <StatCard title="Đang chạy" value={list.hasLoaded ? `${list.jobs.filter((job) => job.status === 'RUNNING').length} job` : '—'} icon="play" tone="success">Trong danh sách đã tải</StatCard>
        <StatCard title="Hoàn thành" value={list.hasLoaded ? `${list.jobs.filter((job) => job.status === 'SUCCEEDED').length} job` : '—'} icon="check" tone="success">Trong danh sách đã tải</StatCard>
      </div>

      <div className="jobs-toolbar">
        <div className="search-field">
          <Icon name="search" size={21} />
          <label className="sr-only" htmlFor="job-search">Tìm theo tên file trong danh sách đã tải</label>
          <input id="job-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)}
            placeholder="Tìm trong các job đã tải" autoComplete="off" />
        </div>
        <div className="filter-tabs" role="group" aria-label="Lọc nhanh trạng thái job">
          {quickFilters.map((filter) => (
            <button key={filter.value} type="button" aria-pressed={status === filter.value}
              className={`filter-tab${status === filter.value ? ' filter-tab--active' : ''}`}
              onClick={() => setStatus(filter.value)}>{filter.label}</button>
          ))}
        </div>
        <div className="select-field">
          <label className="sr-only" htmlFor="job-status">Lọc trạng thái</label>
          <select id="job-status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">Bộ lọc trạng thái</option>
            {statusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <Icon name="chevron-down" size={16} />
        </div>
        <Button variant="secondary" onClick={resetFilters}>Xóa bộ lọc</Button>
        <Button icon="refresh" variant="secondary" disabled={list.loading} onClick={list.refresh}>{list.loading ? 'Đang tải…' : 'Làm mới'}</Button>
      </div>

      {list.error && list.hasLoaded && <div className="notice notice--warning" role="alert"><Icon name="alert" /><div><strong>Chưa làm mới được danh sách</strong><p>{list.error.message} Dữ liệu bên dưới là lần tải trước.</p></div></div>}
      <section className="panel jobs-panel" aria-label="Danh sách job" aria-busy={list.loading || list.loadingMore}>
        {!list.hasLoaded ? (list.error ? <ErrorState message={list.error.message} onRetry={list.refresh} /> : <LoadingState message="Đang tải danh sách job…" />) : <>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Bảng job, có thể cuộn ngang">
          <table className="jobs-table">
            <caption className="sr-only">Danh sách job của bạn, mới nhất trước</caption>
            <thead><tr>
              <th scope="col">Tên job</th><th scope="col">Loại</th><th scope="col">Gói</th>
              <th scope="col">Trạng thái</th><th scope="col">Tiến độ</th><th scope="col">Ngày tạo</th>
              <th scope="col"><span className="sr-only">Chi tiết</span></th>
            </tr></thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.jobId}>
                  <td className="job-name-cell">
                    <Link to={`/jobs/${encodeURIComponent(job.jobId)}`} className="job-name" title={job.name}>{job.name}</Link>
                    {job.progress.total > 1 && <span className="file-count">và {job.progress.total - 1} file khác</span>}
                  </td>
                  <td><span className={`badge badge--${job.kind === 'video' ? 'primary' : 'violet'}`}>{kindLabel(job.kind)}</span></td>
                  <td><span className={`badge badge--${job.tier === 'spot' ? 'success' : 'warning'}`}>{tierLabel(job.tier)}</span></td>
                  <td><StatusBadge status={job.status} /></td>
                  <td><ProgressBar completed={job.progress.succeeded + job.progress.failed} total={job.progress.total}
                    tone={job.status === 'PAUSED' ? 'paused' : job.status === 'SUCCEEDED' ? 'success' : 'primary'} label={`Tiến độ ${job.name}`} /></td>
                  <td className="date-cell">{formatDate(job.createdAt)}</td>
                  <td><Link className="table-action" to={`/jobs/${encodeURIComponent(job.jobId)}`} aria-label={`Xem chi tiết ${job.name}`}><span>Xem chi tiết</span><Icon name="chevron" size={16} /></Link></td>
                </tr>
              ))}
              {jobs.length === 0 && <tr><td colSpan={7}>
                <EmptyState icon={search || status ? 'search' : 'jobs'} title={list.next ? 'Chưa có job phù hợp trong các trang đã tải' : search || status ? 'Không tìm thấy job phù hợp' : 'Bạn chưa có job nào'}
                  description={list.next ? 'Bạn có thể tải thêm trang tiếp theo hoặc thay đổi bộ lọc.' : search || status ? 'Thử tên file khác hoặc bỏ bộ lọc trạng thái.' : 'Tạo job đầu tiên để bắt đầu xử lý file.'}>
                  {search || status ? <Button variant="secondary" onClick={resetFilters}>Xóa bộ lọc</Button> : <ButtonLink to="/jobs/new" icon="plus">Tạo job</ButtonLink>}
                </EmptyState>
              </td></tr>}
            </tbody>
          </table>
        </div>
        </>}
      </section>
      {list.hasLoaded && <div className="list-footer"><span role="status">Hiển thị {jobs.length} / {list.jobs.length} job đã tải{list.next ? ' · Còn trang tiếp theo' : ' · Đã tải hết'}</span>
        {list.next && <Button variant="secondary" disabled={list.loading || list.loadingMore} onClick={list.loadMore}>{list.loadingMore ? 'Đang tải thêm…' : 'Tải thêm'}</Button>}
      </div>}
      {list.moreError && <p className="form-error" role="alert">Chưa tải được trang tiếp theo: {list.moreError.message} Bấm “Tải thêm” để thử lại.</p>}
    </>
  );
}
