import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { Link } from 'react-router';
import { useAuth } from '../auth/AuthContext.jsx';
import { createJobSubmissionApi } from '../api/jobSubmission.js';
import { createUploader } from '../api/upload.js';
import { Button, ButtonLink } from '../components/Button.jsx';
import Icon from '../components/Icon.jsx';
import PageHeader from '../components/PageHeader.jsx';
import StatusBadge from '../components/StatusBadge.jsx';
import { createJobStore } from '../jobs/createJobStore.js';
import { formatBytes } from '../jobs/jobDraft.js';
import LeaveJobDialog from '../jobs/LeaveJobDialog.jsx';
import { formatCredit, kindLabel, tierLabel } from '../utils/format.js';

const uploadFile = createUploader();
const busyPhases = ['creating', 'starting', 'checking', 'cancelling'];
const labels = { waiting: 'Chờ tải', uploading: 'Đang tải', uploaded: 'Đã tải lên', error: 'Chưa tải được' };

function FieldError({ id, children }) {
  return children ? <p id={id} className="form-error" role="alert">{children}</p> : null;
}

function UploadRows({ state, actions }) {
  const editable = state.phase === 'draft';
  const rows = editable ? state.files : state.rows;
  return (
    <ul className="upload-list" aria-label="File của job">
      {rows.map((row, index) => (
        <li className="upload-row" key={row.taskId || index}>
          <Icon name="file" size={22} />
          <div className="upload-file-content">
            <div className="upload-file-heading"><strong title={row.name}>{row.name}</strong><span>{formatBytes(row.size)}</span></div>
            {!editable && <>
              <p className="upload-task-label">{row.taskId} · {labels[row.status]}</p>
              {row.status === 'uploading' && row.measured && <><progress aria-label={`Tải ${row.name}, ${row.taskId}`} max={row.size || 1} value={row.loaded} /><span className="form-help">{formatBytes(row.loaded)} / {formatBytes(row.size)} đã gửi; chờ S3 xác nhận.</span></>}
              {row.error && <p className="upload-file-error" role="alert">{row.error}</p>}
            </>}
          </div>
          {editable ? <Button variant="secondary" onClick={() => actions.removeFile(index)} aria-label={`Bỏ file ${index + 1}: ${row.name}`}>Bỏ</Button>
            : row.status === 'error' && state.phase === 'upload' ? <Button variant="secondary" disabled={row.expired} onClick={() => actions.uploadPending(row.taskId)}>Tải lại</Button>
              : row.status === 'uploaded' ? <span className="badge badge--success"><Icon name="check" size={16} />Đã tải</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function CreateJobView({ state, actions, profile }) {
  const [confirmed, setConfirmed] = useState(false);
  const editable = state.phase === 'draft';
  const done = ['started', 'cancelled'].includes(state.phase);
  const busy = busyPhases.includes(state.phase);
  const complete = state.rows.length > 0 && state.rows.every((row) => row.status === 'uploaded');
  const ready = (state.phase === 'upload' && complete) || state.phase === 'queued';
  const step = state.phase === 'draft' || state.phase === 'creating' || state.phase === 'create-unknown' ? 0 : ready || done || ['starting', 'checking', 'uncertain'].includes(state.phase) ? 2 : 1;
  const cap = Number(state.draft.costCap);
  function confirmCancel() {
    if (window.confirm('Hủy job này? Các lượt upload đang chạy sẽ dừng. File đã tải không tự xóa ngay trên S3.')) actions.cancelJob();
  }
  function newAttempt() {
    if (window.confirm('Bạn đã kiểm tra kết quả tạo job với nhóm? Gửi lại có thể tạo thêm một job khác.')) actions.acknowledgeUnknownCreate();
  }
  return (
    <>
      <PageHeader title="Tạo job mới" description="Chọn file, tải lên và xác nhận chạy trên SpotPool."
        action={<ButtonLink to="/jobs" variant="secondary" icon="arrow-left">Job của tôi</ButtonLink>} />
      <ol className="setup-steps" aria-label="Các bước tạo job">
        {['Cấu hình', 'Tải file', 'Chạy'].map((label, index) => <li key={label} aria-current={step === index ? 'step' : undefined}><span>{index + 1}</span>{label}</li>)}
      </ol>
      {state.error && <p className="form-error create-message" role="alert">{state.error}</p>}
      {state.notice && <div className="notice" role="status"><Icon name="check" /><p>{state.notice}</p></div>}
      {state.profileError && <p className="form-error create-message" role="alert">{state.profileError}</p>}

      {done ? <section className="panel detail-panel">
        <h2>{state.phase === 'cancelled' ? 'Đã hủy job' : 'Đã gửi yêu cầu chạy'}</h2>
        <p className="form-help">Mã job: <span className="mono">{state.jobId}</span></p>
        {state.remoteStatus && <div className="create-message"><StatusBadge status={state.remoteStatus} /></div>}
        <p className="form-help">Theo dõi trạng thái hiện tại tại danh sách job. Task, log và tải kết quả sẽ được hoàn thiện ở bước 5.</p>
        <div className="create-actions"><ButtonLink to="/jobs" icon="jobs">Xem danh sách job</ButtonLink>
          <Button variant="secondary" onClick={() => { setConfirmed(false); actions.reset(); }}>Tạo job khác</Button>
          {state.profileError && <Button variant="secondary" disabled={state.refreshingProfile} onClick={() => actions.refreshCredit()}>Đọc lại credit</Button>}
        </div>
      </section> : <div className="create-job-grid">
        <div className="create-job-main">
          <form className="panel create-panel" aria-label="Cấu hình job" onSubmit={(event) => { event.preventDefault(); actions.create(); }} noValidate>
            <fieldset disabled={!editable}>
              <legend>1. Cấu hình xử lý</legend>
              <div className="form-field"><label htmlFor="job-kind">Loại tác vụ</label>
                <select id="job-kind" value={state.draft.kind} aria-invalid={Boolean(state.errors.kind)} onChange={(event) => actions.configure({ kind: event.target.value })}>
                  <option value="script">Chạy Python</option><option value="video">Nén video</option>
                </select><FieldError>{state.errors.kind}</FieldError>
                <p className="form-help">{state.draft.kind === 'script' ? 'Nhận file .py, kết quả là stdout. Script chạy trong container cách ly, không có mạng để cài thêm thư viện.' : 'Mỗi video là một task. Khả năng xử lý phụ thuộc định dạng/nội dung file; dùng cấu hình mặc định của mẫu video.'}</p>
              </div>
              <div className="form-field"><label htmlFor="job-tier">Gói xử lý</label>
                <select id="job-tier" value={state.draft.tier} aria-invalid={Boolean(state.errors.tier)} onChange={(event) => actions.configure({ tier: event.target.value })}>
                  <option value="spot">Tiết kiệm — Spot</option><option value="ondemand">Ổn định — On-Demand</option>
                </select><FieldError>{state.errors.tier}</FieldError>
                <p className="form-help">{state.draft.tier === 'spot' ? 'Giá tiết kiệm hơn; task có thể bị gián đoạn và tự chạy lại.' : 'Phù hợp khi cần hạn chế gián đoạn do thu hồi Spot.'}</p>
              </div>
              <div className="form-field"><label htmlFor="job-cap">Trần chi phí (credit)</label>
                <input id="job-cap" type="text" inputMode="numeric" autoComplete="off" value={state.draft.costCap} required
                  aria-invalid={Boolean(state.errors.costCap)} aria-describedby={state.errors.costCap ? 'job-cap-error' : 'job-cap-help'}
                  onChange={(event) => actions.configure({ costCap: event.target.value })} />
                <FieldError id="job-cap-error">{state.errors.costCap}</FieldError>
                <p id="job-cap-help" className="form-help">Credit chỉ được giữ khi bạn bấm Chạy job. Hết trần, job có thể tạm dừng.</p>
              </div>
              <p className="form-help">Mỗi task: 1 vCPU và 1,5 GB RAM.</p>
              <div className="form-field"><label htmlFor="job-files">File đầu vào (1–100 file)</label>
                <input key={state.draft.kind} id="job-files" type="file" multiple accept={state.draft.kind === 'script' ? '.py' : 'video/*,.mkv'}
                  aria-invalid={Boolean(state.errors.files)} aria-describedby={state.errors.files ? 'job-files-help job-files-error' : 'job-files-help'} onChange={(event) => {
                    if (event.target.files.length) actions.setFiles(event.target.files);
                    event.target.value = '';
                  }} />
                <p id="job-files-help" className="form-help">Chọn lại để thay danh sách. Thứ tự file được giữ nguyên; các file trùng tên vẫn là những task riêng.</p>
              </div>
            </fieldset>
            <FieldError id="job-files-error">{state.errors.files}</FieldError>
            {editable && <UploadRows state={state} actions={actions} />}
            {editable && <div className="create-actions"><Button type="submit" icon="arrow" disabled={!state.files.length}>Tạo job và tải file</Button></div>}
            {state.phase === 'creating' && <p className="form-help" role="status">Đang tạo job…</p>}
            {!editable && <p className="form-help">Cấu hình đã khóa. Để đổi file, loại hoặc ngân sách, hãy hủy job này trước.</p>}
          </form>

          {state.jobId && <section className="panel create-panel" aria-label="Tải file và chạy job">
            <h2>2. Tải file</h2>
            <p className="form-help">{state.rows.filter((row) => row.status === 'uploaded').length} / {state.rows.length} file đã được S3 xác nhận. Link tải lên có thời hạn; file và link chỉ được giữ trong trang này.</p>
            <UploadRows state={state} actions={actions} />
            {state.phase === 'upload' && state.rows.some((row) => row.status === 'error' && !row.expired) && <Button variant="secondary" onClick={() => actions.uploadPending()}>Tải lại các file lỗi</Button>}
            <div className="create-start-section">
              <h2>3. Xác nhận chạy</h2>
              <label className="checkbox-field"><input type="checkbox" checked={confirmed} disabled={!ready || busy}
                onChange={(event) => setConfirmed(event.target.checked)} />Tôi xác nhận giữ {formatCredit(cap)} credit để chạy job này.</label>
              <div className="create-actions">
                <Button icon="play" disabled={!ready || !confirmed || busy} onClick={() => actions.start()}>{state.phase === 'starting' ? 'Đang gửi…' : state.phase === 'queued' ? 'Hoàn tất gửi job' : 'Chạy job'}</Button>
                {state.phase === 'uncertain' && <Button variant="secondary" onClick={() => actions.checkStatus()}>Kiểm tra trạng thái</Button>}
                {['upload', 'queued', 'create-unknown'].includes(state.phase) && <Button variant="secondary" onClick={confirmCancel}>Hủy job này</Button>}
              </div>
              {busy && <p className="form-help" role="status">{state.phase === 'checking' ? 'Đang kiểm tra trạng thái từ API…' : state.phase === 'cancelling' ? 'Đang hủy job…' : 'Đang gửi yêu cầu…'}</p>}
            </div>
          </section>}
          {state.phase === 'create-unknown' && !state.jobId && <section className="panel create-panel">
            <h2>Kiểm tra kết quả trước khi tạo lại</h2><p className="form-help">Một job có thể đã được tạo nhưng web chưa nhận được mã. Kiểm tra danh sách hoặc phối hợp với nhóm để xác định trước.</p>
            <div className="create-actions"><ButtonLink to="/jobs" variant="secondary">Mở danh sách job</ButtonLink><Button variant="secondary" onClick={newAttempt}>Tôi đã kiểm tra, cho phép tạo lại</Button></div>
          </section>}
        </div>
        <aside className="panel create-summary" aria-label="Tóm tắt job">
          <h2>Tóm tắt</h2>
          <dl className="key-values"><div><dt>Loại</dt><dd>{kindLabel(state.draft.kind)}</dd></div><div><dt>Gói</dt><dd>{tierLabel(state.draft.tier)}</dd></div>
            <div><dt>Số file</dt><dd>{state.files.length}</dd></div><div><dt>Trần chi phí</dt><dd>{Number.isSafeInteger(cap) && cap > 0 ? `${formatCredit(cap)} credit` : '—'}</dd></div>
            <div><dt>Credit khả dụng</dt><dd>{formatCredit(profile.creditAvailable)}</dd></div><div><dt>Đang dùng hạn mức</dt><dd>{profile.runningJobs} / {profile.maxConcurrentJobs} job</dd></div></dl>
          {(cap > profile.creditAvailable || profile.runningJobs >= profile.maxConcurrentJobs) && <p className="form-error">Số dư hoặc hạn mức hiện tại có thể chưa đủ để chạy. API sẽ kiểm tra lại khi bạn xác nhận chạy.</p>}
          {state.jobId && <p className="form-help">Mã job: <span className="mono">{state.jobId}</span></p>}
          <p className="form-help">Tạo job và upload chưa giữ credit. API quyết định số dư, hạn mức và giá xử lý thực tế.</p>
          <Link to="/account" className="text-link">Xem tài khoản</Link>
        </aside>
      </div>}
    </>
  );
}

export default function CreateJobPage() {
  const { profile, request, refreshProfile, getSessionSignal } = useAuth();
  const sessionSignal = getSessionSignal();
  const store = useMemo(() => createJobStore({ api: createJobSubmissionApi({ request }), uploadFile, refreshProfile }), [request, refreshProfile, profile.userId, sessionSignal]);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => {
    const end = () => store.cancel();
    if (sessionSignal.aborted) end();
    sessionSignal.addEventListener('abort', end, { once: true });
    return () => { sessionSignal.removeEventListener('abort', end); store.cancel(); };
  }, [store, sessionSignal]);
  const warn = state.files.length > 0 && !['started', 'cancelled'].includes(state.phase);
  return <><CreateJobView key={profile.userId} state={state} actions={store} profile={profile} /><LeaveJobDialog when={warn} /></>;
}
