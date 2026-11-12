import { useEffect, useRef } from 'react';
import { useParams } from 'react-router';
import { Button, ButtonLink } from '../components/Button.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import ProgressBar from '../components/ProgressBar.jsx';
import Icon from '../components/Icon.jsx';
import { useJobDetail } from '../jobs/useJobDetail.js';
import { isActiveJob, needsSettlement } from '../jobs/jobDetailStore.js';
import { formatCredit, formatDateTime, kindLabel, tierLabel } from '../utils/format.js';

function resumeNote(kind, subject) {
  return kind === 'script'
    ? `${subject} chạy tiếp từ checkpoint gần nhất nếu script có lưu vào thư mục checkpoint, nếu không thì chạy lại từ đầu.`
    : `${subject} sẽ chạy lại từ đầu.`;
}

function LogDialog({ log, files }) {
  const ref = useRef(null);
  const open = Boolean(log);
  useEffect(() => {
    if (open && !ref.current.open) ref.current.showModal();
    else if (!open && ref.current.open) ref.current.close();
  }, [open]);
  return <dialog className="log-dialog" ref={ref} aria-labelledby="task-log-title"
    onCancel={(event) => { event.preventDefault(); files.closeLog(); }}>
    {log && <>
      <div className="log-heading"><div><h2 id="task-log-title">Log của task</h2><p className="form-help">{log.taskId} · {log.fileName}</p></div>
        <Button variant="secondary" icon="close" onClick={files.closeLog}>Đóng</Button></div>
      {log.loading ? <LoadingState message="Đang đọc log…" /> : log.error ? <ErrorState message={log.error} onRetry={() => files.showLog(log.taskId)} /> : <>
        {log.truncated && <p className="notice notice--warning">Log đã được rút gọn, chỉ hiển thị tối đa 1 MiB đầu.</p>}
        <pre className="log-content" tabIndex={0} aria-label="Nội dung log">{log.text || 'Log hiện chưa có nội dung.'}</pre>
        {log.url && <a className="button button--secondary" href={log.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Mở log đầy đủ</a>}
        <p className="form-help">Link có thời hạn. Nếu không mở được, đóng và bấm Xem log để lấy lại link.</p>
      </>}
    </>}
  </dialog>;
}

export function JobDetailView({ jobId, state, fileState, cancelState = {}, cancelAction, refresh, files }) {
  const { job, loading, error } = state;
  const completed = job ? job.progress.succeeded + job.progress.failed : 0;
  const interrupted = job?.interrupted && ['QUEUED', 'RUNNING'].includes(job.status);
  function confirmCancel() {
    if (window.confirm('Hủy job này? Task đang chạy có thể cần khoảng 30 giây để dừng. Phần đã chạy vẫn được quyết toán.')) cancelAction.cancel();
  }
  return <>
    <PageHeader title="Chi tiết job" description={`Mã job: ${jobId}`}
      action={<div className="detail-actions"><ButtonLink to="/jobs" variant="secondary" icon="arrow-left">Job của tôi</ButtonLink>
        <Button icon="refresh" variant="secondary" disabled={loading} onClick={refresh}>{loading ? 'Đang tải…' : 'Làm mới'}</Button>
        {cancelAction && isActiveJob(job) && <Button variant="secondary" disabled={cancelState.busy || cancelState.unknown} onClick={confirmCancel}>{cancelState.busy ? 'Đang kiểm tra…' : 'Hủy job'}</Button>}</div>} />
    {cancelState.notice && <div className="notice" role="status"><p>{cancelState.notice}</p></div>}
    {cancelState.error && <div className="notice notice--warning" role="alert"><p>{cancelState.error}</p>
      {cancelState.unknown && <Button variant="secondary" disabled={cancelState.busy} onClick={cancelAction.checkStatus}>Kiểm tra trạng thái</Button>}</div>}
    {cancelState.profileError && <p className="form-error create-message" role="alert">{cancelState.profileError}</p>}
    {!job ? <section className="panel">{error ? <ErrorState message={error.message} onRetry={refresh} /> : <LoadingState message="Đang tải chi tiết job…" />}</section> : <>
      {error && <div className="notice notice--warning" role="alert"><Icon name="alert" /><p>{error.message} Dữ liệu bên dưới là lần tải trước.</p></div>}
      <div className="detail-labels"><StatusBadge status={job.status} /><span className="badge badge--violet">{kindLabel(job.kind)}</span>
        <span className="badge badge--neutral">{tierLabel(job.tier)}</span></div>
      {interrupted && <div className="notice notice--warning" role="status"><Icon name="alert" /><p>Bị gián đoạn, đang chạy tiếp. {resumeNote(job.kind, 'Task bị gián đoạn')}</p></div>}
      {job.status === 'PAUSED' && <div className="notice notice--warning"><Icon name="alert" /><p>Job đang tạm dừng vì đã chạm trần chi phí. Khi được nâng trần và chạy tiếp, {resumeNote(job.kind, 'task bị dừng')} Phần đã chạy vẫn được tính mức dùng.</p></div>}
      {job.status === 'UPLOADING' && <div className="notice"><Icon name="file" /><p>Job đang chờ tải đủ file và xác nhận chạy. Nếu trang tạo job vẫn đang mở, hãy hoàn tất tại đó. File chưa tải không được giữ sau khi rời trang tạo job.</p></div>}
      {job.status === 'CANCELLED' && <div className="notice"><Icon name="alert" /><p>Job đã nhận yêu cầu hủy. Task đang chạy có thể cần tới lần kiểm tra tiếp theo, khoảng 30 giây, để dừng.</p></div>}
      <div className="detail-grid">
        <section className="panel detail-panel" aria-label="Tiến độ job"><h2>Tiến độ xử lý</h2>
          <p className="detail-progress-value">{completed}<span> / {job.progress.total} task đã kết thúc</span></p>
          <ProgressBar completed={completed} total={job.progress.total} tone={job.status === 'PAUSED' ? 'paused' : job.status === 'SUCCEEDED' ? 'success' : 'primary'} label="Tiến độ job" />
          <div className="progress-summary"><span>Thành công: <strong>{job.progress.succeeded}</strong></span><span>Thất bại: <strong>{job.progress.failed}</strong></span></div>
          <p className="form-help">Tiến độ tính theo task thành công hoặc thất bại; task bị hủy không được cộng vào số đã xử lý.</p>
          <p className="form-help">{isActiveJob(job) ? 'Tự động cập nhật.' : needsSettlement(job) && !state.settlementTimedOut ? 'Đang chờ tính phí.' : 'Bấm Làm mới để cập nhật.'}</p>
          {state.updatedAt != null && <p className="form-help">Cập nhật: {formatDateTime(Math.floor(state.updatedAt / 1000))}</p>}
        </section>
        <section className="panel detail-panel" aria-label="Chi phí và cấu hình"><h2>Chi phí và cấu hình</h2>
          <dl className="key-values">
            <div><dt>Gói đã chọn</dt><dd>{tierLabel(job.tier)}</dd></div><div><dt>Tài nguyên mỗi task</dt><dd>{job.vcpu} vCPU · {job.vcpu * 1.5} GB RAM</dd></div>
            <div><dt>Trần chi phí</dt><dd>{formatCredit(job.costCap)} credit</dd></div><div><dt>Mức dùng</dt><dd>{formatCredit(job.usedVcpuSeconds)} vCPU-giây</dd></div>
            <div><dt>Phí quyết toán</dt><dd>{job.chargedCredit !== null ? `${formatCredit(job.chargedCredit)} credit` : needsSettlement(job) ? 'Chờ quyết toán' : job.status === 'CANCELLED' && job.queuedAt === null ? 'Chưa chạy' : 'Chưa quyết toán'}</dd></div>
          </dl>
          {state.settlementTimedOut && <p className="form-help" role="status">Chưa có kết quả quyết toán. Bấm Làm mới để kiểm tra lại sau.</p>}
        </section>
      </div>
      <section className="panel detail-panel detail-timeline" aria-label="Thời gian của job"><h2>Thời gian</h2><dl className="key-values">
        {[['createdAt', 'Tạo job'], ['queuedAt', 'Vào hàng đợi'], ['startedAt', 'Bắt đầu chạy'], ['finishedAt', 'Kết thúc']].map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{formatDateTime(job[key])}</dd></div>)}
      </dl><p className="form-help">Giờ Việt Nam (UTC+7).</p></section>
      <section className="panel jobs-panel" aria-label="Các task của job">
        <div className="detail-panel task-heading"><h2>File và kết quả</h2><p className="form-help">Kết quả Python dạng văn bản; video dạng MP4. File được lưu tối đa 7 ngày.</p></div>
        {job.tasks.length === 0 ? <EmptyState title={['UPLOADING', 'QUEUED', 'RUNNING'].includes(job.status) ? 'Đang chuẩn bị các task' : 'Chưa có task để hiển thị'} description="Làm mới để kiểm tra lại danh sách task." /> : <div className="table-scroll" role="region" tabIndex={0} aria-label="Bảng task, có thể cuộn ngang">
          <table className="jobs-table tasks-table"><caption className="sr-only">Trạng thái, log và kết quả của từng task</caption>
            <thead><tr><th scope="col">File</th><th scope="col">Trạng thái</th><th scope="col">Gián đoạn</th>{job.kind === 'script' && <th scope="col">Checkpoint</th>}<th scope="col">Log</th><th scope="col">Kết quả</th></tr></thead>
            <tbody>{job.tasks.map((task) => <tr key={task.taskId}>
              <td className="task-file"><strong title={task.fileName}>{task.fileName}</strong><span className="file-count mono">{task.taskId}</span></td>
              <td><StatusBadge status={task.status} /></td><td>{task.interruptCount} lần</td>
              {job.kind === 'script' && <td>{task.checkpointAt ? <span title="Lần gần nhất nền tảng lưu thư mục checkpoint của script">Lưu lúc {formatDateTime(task.checkpointAt)}</span> : <span className="form-help">Chưa lưu</span>}</td>}
              <td>{task.logUrl ? <Button variant="secondary" onClick={() => files.showLog(task.taskId)}>Xem log</Button> : <span className="form-help">Chưa có log</span>}</td>
              <td>{task.status === 'SUCCEEDED' && task.resultUrl ? <Button variant="secondary" icon="arrow" disabled={fileState.download?.loading} onClick={() => files.downloadResult(task.taskId)}>{fileState.download?.loading && fileState.download.taskId === task.taskId ? 'Đang lấy link…' : 'Tải kết quả'}</Button> : <span className="form-help">{task.status === 'SUCCEEDED' ? 'Chưa có file kết quả' : 'Chưa có kết quả'}</span>}</td>
            </tr>)}</tbody>
          </table>
        </div>}
      </section>
      {fileState.download?.error && <p className="form-error create-message" role="alert">{fileState.download.error}</p>}
      {fileState.download?.url && <div className="notice"><p>Trình duyệt chưa mở tab tải file. <a className="text-link" href={fileState.download.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Bấm để mở kết quả</a>. Nếu link hết hạn, bấm Tải kết quả để lấy link mới.</p></div>}
    </>}
    <LogDialog log={job ? fileState.log : null} files={files} />
  </>;
}

export default function JobDetailPage() {
  const { jobId } = useParams();
  return <JobDetailView jobId={jobId} {...useJobDetail(jobId)} />;
}
