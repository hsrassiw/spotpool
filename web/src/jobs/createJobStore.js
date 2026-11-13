import { MAX_FILE_BYTES, MAX_FILES, validateJobDraft } from './jobDraft.js';

const initial = () => ({ phase: 'draft', draft: { kind: 'script', tier: 'spot', costCap: '5000' }, files: [], rows: [],
  jobId: null, remoteStatus: null, errors: {}, error: null, notice: null, profileError: null, refreshingProfile: false });

export function createJobStore({ api, uploadFile, refreshProfile, now = Date.now }) {
  let state = Object.freeze(initial());
  let files = [];
  let links = new Map();
  let epoch = 0;
  let controller;
  let pendingAction = 'start';
  let lastFailure = null;
  let queue = [];
  const active = new Map();
  const waiters = new Set();
  const listeners = new Set();
  const update = (patch) => { state = Object.freeze({ ...state, ...patch }); listeners.forEach((fn) => fn()); };
  const rowUpdate = (id, patch) => update({ rows: state.rows.map((row) => row.taskId === id ? { ...row, ...patch } : row) });
  const idle = () => { if (!queue.length && !active.size) { waiters.forEach((resolve) => resolve()); waiters.clear(); } };
  const waitIdle = () => !queue.length && !active.size ? Promise.resolve() : new Promise((resolve) => waiters.add(resolve));

  function cancel() {
    epoch += 1;
    controller?.abort();
    active.forEach((item) => item.abort());
    active.clear(); queue = [];
    idle();
  }
  function begin() {
    cancel(); controller = new AbortController();
    if (state.refreshingProfile) update({ refreshingProfile: false });
    return { current: epoch, signal: controller.signal };
  }

  function pump() {
    while (state.phase === 'upload' && queue.length && active.size < 3) {
      const id = queue.shift();
      const link = links.get(id);
      const row = state.rows.find((item) => item.taskId === id);
      if (!link || !row || row.status === 'uploaded') continue;
      if (now() >= link.expiresAt) {
        rowUpdate(id, { status: 'error', expired: true, error: 'Link tải lên đã hết hạn. Hãy hủy job này và tạo job mới khi sẵn sàng.' });
        continue;
      }
      const current = epoch;
      const uploadController = new AbortController();
      active.set(id, uploadController);
      rowUpdate(id, { status: 'uploading', loaded: 0, measured: false, error: null });
      Promise.resolve().then(() => {
        if (current !== epoch) throw new DOMException('Upload cancelled', 'AbortError');
        return uploadFile(link.url, files[link.index], {
          signal: uploadController.signal,
          onProgress: ({ loaded }) => {
            if (current === epoch && !uploadController.signal.aborted) rowUpdate(id, { loaded: Math.max(0, Math.min(row.size, loaded)), measured: true });
          },
        });
      }).then(() => {
        if (current === epoch) rowUpdate(id, { status: 'uploaded', loaded: row.size, measured: true, error: null });
      }).catch((error) => {
        if (current === epoch) rowUpdate(id, { status: 'error', error: error.name === 'AbortError' ? 'Lượt tải đã dừng. Bạn có thể thử lại.' : error.message });
      }).finally(() => {
        if (current !== epoch) return;
        active.delete(id); pump(); idle();
      });
    }
    idle();
  }

  async function uploadPending(taskId) {
    if (state.phase !== 'upload') return;
    state.rows.forEach((row) => {
      if ((!taskId || row.taskId === taskId) && !row.expired && ['waiting', 'error'].includes(row.status)
        && !active.has(row.taskId) && !queue.includes(row.taskId)) {
        queue.push(row.taskId);
        rowUpdate(row.taskId, { status: 'waiting', loaded: 0, measured: false, error: null });
      }
    });
    pump();
    await waitIdle();
  }

  async function refreshCredit(current = epoch) {
    if (!refreshProfile || state.refreshingProfile) return;
    update({ refreshingProfile: true, profileError: null });
    try { await refreshProfile(); }
    catch (error) { if (current === epoch && error.name !== 'AbortError') update({ profileError: 'Chưa đọc lại được credit. Mở Tài khoản để kiểm tra số dư mới nhất.' }); }
    finally { if (current === epoch) update({ refreshingProfile: false }); }
  }

  async function create() {
    if (state.phase !== 'draft') return;
    const { errors, payload } = validateJobDraft(state.draft, files);
    if (!payload) { update({ errors }); return; }
    const { current, signal } = begin();
    const requestedAt = now();
    update({ phase: 'creating', errors: {}, error: null, notice: null });
    try {
      const job = await api.createJob(payload, { signal });
      if (current !== epoch) return;
      links = new Map(job.uploads.map((item, index) => [item.taskId, { url: item.url, index, expiresAt: requestedAt + item.expiresIn * 1000 }]));
      update({ phase: 'upload', jobId: job.jobId, remoteStatus: 'UPLOADING', rows: job.uploads.map((item, index) => ({
        taskId: item.taskId, name: files[index].name, size: files[index].size, status: 'waiting', loaded: 0, measured: false, error: null, expired: false,
      })) });
      await uploadPending(); // Không tự start sau khi upload xong.
    } catch (error) {
      if (current !== epoch) return;
      const rejected = error.code === 'CONFIG' || (error.status >= 400 && error.status < 500 && error.status !== 408);
      update({ phase: rejected ? 'draft' : 'create-unknown', error: rejected ? error.message
        : error.details?.jobId ? 'Đã nhận mã job nhưng thông tin tải lên chưa hợp lệ. Hãy hủy job này trước khi tạo lại.'
          : 'Chưa xác định được kết quả tạo job. Gửi lại có thể tạo thêm một job. Hãy kiểm tra danh sách job trước khi tạo lại.',
        jobId: error.details?.jobId ?? null });
    }
  }

  function reconcile(action, result) {
    const status = result.status;
    if (status === 'CANCELLED') {
      files = []; links.clear();
      update({ phase: 'cancelled', remoteStatus: status, error: null, notice: 'Job đã được hủy.' });
    } else if (action === 'cancel' && !['SUCCEEDED', 'FAILED'].includes(status)) {
      update({ phase: status === 'UPLOADING' ? 'upload' : 'uncertain', remoteStatus: status,
        error: `Chưa xác nhận hủy job. Trạng thái đọc được: ${status}. ${lastFailure?.message || ''}` });
    } else if (status === 'UPLOADING') {
      const missing = lastFailure?.details?.missingFiles || [];
      update({ phase: 'upload', remoteStatus: status, error: lastFailure?.message || 'Job vẫn đang chờ tải file. Bạn có thể thử lại yêu cầu chạy trên job này.',
        rows: state.rows.map((row) => missing.includes(row.taskId) ? { ...row, status: 'error', loaded: 0, measured: false, error: 'S3 báo thiếu file này. Hãy tải lại.', expired: now() >= links.get(row.taskId)?.expiresAt } : row) });
    } else if (status === 'QUEUED') {
      update({ phase: 'queued', remoteStatus: status,
        error: 'Job đã QUEUED, credit có thể đã được giữ. Yêu cầu trước có thể chưa ghi xong thông tin gửi job. Bấm “Hoàn tất gửi job” để xác nhận lại trên cùng job.', notice: null });
    } else {
      update({ phase: 'started', remoteStatus: status, error: null, notice: `Trạng thái job từ API: ${status}.` });
    }
  }

  async function recover(current, signal) {
    try {
      const result = await api.getJobState(state.jobId, { signal });
      if (current !== epoch) return;
      reconcile(pendingAction, result);
    } catch {
      if (current === epoch) update({ phase: 'uncertain', error: 'Chưa kiểm tra được trạng thái job. Hãy bấm “Kiểm tra trạng thái” trước khi thao tác tiếp; không tạo job mới để thử lại.' });
    }
    if (current === epoch) await refreshCredit(current);
  }

  async function start() {
    const ready = state.phase === 'queued' || (state.phase === 'upload' && state.rows.length > 0 && state.rows.every((row) => row.status === 'uploaded'));
    if (!ready || active.size || queue.length) return;
    const { current, signal } = begin();
    pendingAction = 'start'; lastFailure = null;
    update({ phase: 'starting', error: null });
    try {
      await api.startJob(state.jobId, { signal });
      if (current !== epoch) return;
      update({ phase: 'started', remoteStatus: 'QUEUED', notice: 'API đã xác nhận gửi job vào hàng đợi. Credit được giữ theo trần chi phí.', error: null });
      // Hai lần đọc độc lập; lỗi đọc sau start không biến thành một lần start thất bại.
      await Promise.all([
        refreshCredit(current),
        api.getJobState(state.jobId, { signal }).then((result) => {
          if (current === epoch) update({ remoteStatus: result.status });
        }).catch(() => { if (current === epoch) update({ notice: 'API đã xác nhận gửi job; chưa đọc được trạng thái mới nhất. Hãy làm mới danh sách job.' }); }),
      ]);
    } catch (error) {
      if (current !== epoch) return;
      lastFailure = error;
      update({ phase: 'checking', error: 'Đang kiểm tra kết quả yêu cầu chạy trên cùng job…' });
      await recover(current, signal);
    }
  }

  async function cancelJob() {
    if (!state.jobId || !['upload', 'queued', 'create-unknown'].includes(state.phase)) return;
    const { current, signal } = begin(); // Dừng PUT trước khi gửi yêu cầu hủy.
    pendingAction = 'cancel'; lastFailure = null;
    update({ phase: 'cancelling', error: null, rows: state.rows.map((row) => row.status === 'uploading' || row.status === 'waiting'
      ? { ...row, status: 'error', error: 'Lượt tải đã dừng.', loaded: 0, measured: false } : row) });
    try {
      await api.cancelJob(state.jobId, { signal });
      if (current !== epoch) return;
      reconcile('cancel', { status: 'CANCELLED' });
      await refreshCredit(current);
    } catch (error) {
      if (current !== epoch) return;
      lastFailure = error;
      update({ phase: 'checking', error: 'Đang kiểm tra kết quả yêu cầu hủy…' });
      await recover(current, signal);
    }
  }

  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    getSnapshot: () => state,
    configure(patch) {
      if (state.phase !== 'draft') return;
      update({ draft: { ...state.draft, ...Object.fromEntries(Object.entries(patch).filter(([key]) => ['kind', 'tier', 'costCap'].includes(key))) }, errors: {}, error: null });
    },
    setFiles(selected) {
      if (state.phase !== 'draft') return;
      const next = Array.from(selected);
      if (next.length > MAX_FILES) { update({ errors: { ...state.errors, files: 'Tối đa 100 file trong một job. Danh sách cũ được giữ nguyên.' } }); return; }
      files = next;
      const tooLarge = files.filter((file) => file.size > MAX_FILE_BYTES).map((file) => file.name);
      const errors = tooLarge.length ? { files: `Mỗi file tối đa 500 MB. File quá lớn: ${tooLarge.join(', ')}.` } : {};
      update({ files: files.map((file, index) => ({ index, name: file.name, size: file.size })), errors, error: null });
    },
    removeFile(index) {
      if (state.phase !== 'draft') return;
      files = files.filter((_file, position) => position !== index);
      update({ files: files.map((file, position) => ({ index: position, name: file.name, size: file.size })), errors: {}, error: null });
    },
    create, uploadPending, start, cancelJob, refreshCredit, cancel,
    async checkStatus() {
      if (!state.jobId || state.phase !== 'uncertain') return;
      const { current, signal } = begin();
      update({ phase: 'checking', error: null });
      await recover(current, signal);
    },
    acknowledgeUnknownCreate() {
      if (state.phase === 'create-unknown' && !state.jobId) update({ phase: 'draft', error: null });
    },
    reset() {
      if (!['started', 'cancelled'].includes(state.phase)) return;
      cancel(); files = []; links.clear(); update(initial());
    },
  };
}
