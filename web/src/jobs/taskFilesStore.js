import { ApiError } from '../api/client.js';

const unavailable = () => new ApiError('File chưa sẵn sàng hoặc đã hết thời hạn lưu. Hãy làm mới job rồi thử lại.', { code: 'FILE_UNAVAILABLE' });

export function createTaskFilesStore({ getCurrentJob, refreshJob, readLog, openWindow }) {
  let state = Object.freeze({ log: null, download: null });
  let active = true;
  let logEpoch = 0;
  let downloadEpoch = 0;
  let logController;
  let pendingWindow;
  const listeners = new Set();
  const update = (values) => { state = Object.freeze({ ...state, ...values }); listeners.forEach((listener) => listener()); };
  const findTask = (job, taskId) => job?.tasks.find((task) => task.taskId === taskId);
  const message = (error) => error instanceof ApiError ? error.message : 'Chưa đọc được file. Vui lòng thử lại.';

  function closeLog() {
    logEpoch += 1;
    logController?.abort();
    update({ log: null });
  }

  async function showLog(taskId) {
    if (!active) return;
    closeLog();
    const current = logEpoch;
    const task = findTask(getCurrentJob(), taskId);
    if (!task?.logUrl) return;
    logController = new AbortController();
    const signal = logController.signal;
    const valid = () => active && current === logEpoch && !signal.aborted;
    update({ log: { taskId, fileName: task.fileName, loading: true, text: '', truncated: false, url: null, error: null } });
    try {
      let url = task.logUrl;
      let result;
      try { result = await readLog(url, { signal }); }
      catch (error) {
        if (!valid()) return;
        // Một lần cấp lại URL cho thao tác này, kể cả khi CORS che lỗi 403 từ S3.
        if (error.code === 'FILE_TIMEOUT') throw error;
        const job = await refreshJob();
        if (!valid()) return;
        url = findTask(job, taskId)?.logUrl;
        if (!url) throw unavailable();
        result = await readLog(url, { signal });
      }
      if (valid()) update({ log: { taskId, fileName: task.fileName, loading: false, ...result, url, error: null } });
    } catch (error) {
      if (valid()) update({ log: { ...state.log, loading: false, error: message(error) } });
    }
  }

  async function downloadResult(taskId) {
    if (!active || state.download?.loading) return;
    const initial = findTask(getCurrentJob(), taskId);
    if (initial?.status !== 'SUCCEEDED' || !initial.resultUrl) return;
    const current = ++downloadEpoch;
    let target;
    // Mở tab trong sự kiện click, rồi lấy URL mới trước khi chuyển tới S3.
    try { target = openWindow(); if (target) target.opener = null; } catch { target = null; }
    pendingWindow = target;
    update({ download: { taskId, loading: true, url: null, error: null } });
    try {
      const job = await refreshJob();
      if (!active || current !== downloadEpoch) return;
      const task = findTask(job, taskId);
      if (task?.status !== 'SUCCEEDED' || !task.resultUrl) throw unavailable();
      if (target && !target.closed) target.location.replace(task.resultUrl);
      update({ download: { taskId, loading: false, url: target && !target.closed ? null : task.resultUrl, error: null } });
    } catch (error) {
      target?.close();
      if (active && current === downloadEpoch) update({ download: { taskId, loading: false, url: null, error: message(error) } });
    } finally {
      if (current === downloadEpoch) pendingWindow = null;
    }
  }

  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getSnapshot: () => state,
    showLog, closeLog, downloadResult,
    start() { active = true; },
    stop() {
      active = false;
      downloadEpoch += 1;
      pendingWindow?.close();
      pendingWindow = null;
      closeLog();
      update({ download: null });
    },
  };
}
