const activeStatuses = ['UPLOADING', 'QUEUED', 'RUNNING', 'PAUSED'];
export const isActiveJob = (job) => Boolean(job && activeStatuses.includes(job.status));
export const needsSettlement = (job) => Boolean(job && !isActiveJob(job) && job.chargedCredit === null
  && (job.queuedAt !== null || job.startedAt !== null));

// Chỉ một GET đang chạy. Timer được đặt sau khi request hoàn tất.
export function createJobDetailStore({ loadJob, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout,
  pollMs = 5000, settlementMs = 60000 }) {
  let state = Object.freeze({ job: null, loading: true, error: null, updatedAt: null, settlementTimedOut: false });
  let active = false;
  let visible = true;
  let epoch = 0;
  let timer;
  let controller;
  let pending;
  let settlementStarted = null;
  const listeners = new Set();
  const update = (values) => { state = Object.freeze({ ...state, ...values }); listeners.forEach((listener) => listener()); };
  const clearSchedule = () => { clearTimer(timer); timer = undefined; };
  const settlementExpired = () => needsSettlement(state.job) && settlementStarted !== null && now() - settlementStarted >= settlementMs;

  function schedule() {
    clearSchedule();
    if (!active || !visible || !state.job) return;
    if (settlementExpired()) { update({ settlementTimedOut: true }); return; }
    if (!isActiveJob(state.job) && !needsSettlement(state.job)) return;
    const delay = needsSettlement(state.job) ? Math.min(pollMs, settlementMs - (now() - settlementStarted)) : pollMs;
    timer = setTimer(() => {
      timer = undefined;
      if (settlementExpired()) update({ settlementTimedOut: true });
      else refresh();
    }, delay);
  }

  function refresh() {
    if (!active || !visible) return Promise.resolve(null);
    if (pending) return pending;
    clearSchedule();
    const current = epoch;
    controller = new AbortController();
    const signal = controller.signal;
    update({ loading: true, error: null });
    pending = Promise.resolve().then(() => loadJob({ signal })).then((job) => {
      if (current !== epoch) return null;
      if (needsSettlement(job)) settlementStarted ??= now();
      else settlementStarted = null;
      update({ job, updatedAt: now(), error: null, settlementTimedOut: false });
      return job;
    }).catch((error) => {
      if (current !== epoch) return null;
      // Không giữ dữ liệu/link cũ nếu quyền truy cập hoặc job đã không còn.
      update({ error, ...([401, 403, 404].includes(error.status) ? { job: null } : {}) });
      return null;
    }).finally(() => {
      if (current !== epoch) return;
      pending = null;
      update({ loading: false });
      schedule();
    });
    return pending;
  }

  // Cho thao tác người dùng bấm (xem log, tải kết quả): không phụ thuộc trang đang hiện hay ẩn,
  // vì mở tab tải file làm trang job bị ẩn giữa chừng. Lỗi được ném ra để báo đúng nguyên nhân.
  async function load() {
    if (!active) return null;
    const current = epoch;
    const job = await loadJob({});
    if (active && current === epoch && !pending) update({ job, updatedAt: now(), error: null });
    return active ? job : null;
  }

  function suspend() {
    epoch += 1;
    clearSchedule();
    controller?.abort();
    pending = null;
    update({ loading: false });
  }

  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getSnapshot: () => state,
    refresh,
    load,
    start(isVisible = true) { active = true; visible = isVisible; return refresh(); },
    stop() { active = false; suspend(); },
    setVisible(isVisible) {
      if (visible === isVisible) return;
      visible = isVisible;
      if (!visible) suspend();
      else if (active) return refresh();
    },
  };
}
