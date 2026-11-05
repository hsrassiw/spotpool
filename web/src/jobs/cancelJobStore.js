import { ApiError } from '../api/client.js';
import { isActiveJob } from './jobDetailStore.js';

// Không tự retry POST: một lỗi mạng vẫn có thể xảy ra sau khi server đã hủy job.
export function createCancelJobStore({ getJob, cancelJob, refreshJob, refreshProfile }) {
  let state = Object.freeze({ busy: false, unknown: false, error: null, notice: null, profileError: null });
  let active = true;
  let epoch = 0;
  let controller;
  const listeners = new Set();
  const update = (values) => { state = Object.freeze({ ...state, ...values }); listeners.forEach((listener) => listener()); };

  async function reconcile(mutationError, acknowledged, current) {
    const [jobResult, profileResult] = await Promise.allSettled([refreshJob(), refreshProfile()]);
    if (!active || current !== epoch) return;
    const job = jobResult.status === 'fulfilled' ? jobResult.value : null;
    const profileError = profileResult.status === 'rejected' ? 'Chưa cập nhật được số dư. Mở tài khoản và làm mới để kiểm tra lại.' : null;
    if (job?.status === 'CANCELLED') {
      update({ unknown: false, error: null, notice: 'Đã nhận yêu cầu hủy job. Task đang chạy có thể cần khoảng 30 giây để dừng.', profileError });
    } else if (job && !isActiveJob(job)) {
      update({ unknown: false, error: null, notice: 'Job đã kết thúc. Trạng thái mới nhất được hiển thị bên dưới.', profileError });
    } else if (!job || acknowledged) {
      update({ unknown: true, error: 'Chưa xác nhận được trạng thái sau yêu cầu hủy. Bấm Kiểm tra trạng thái trước khi thử lại.', notice: null, profileError });
    } else {
      update({ unknown: false, error: mutationError instanceof ApiError ? mutationError.message : 'Job vẫn đang hoạt động. Bạn có thể kiểm tra lại rồi yêu cầu hủy.', notice: null, profileError });
    }
  }

  async function run(checkOnly) {
    if (!active || state.busy || (!checkOnly && (state.unknown || !isActiveJob(getJob())))) return;
    const current = ++epoch;
    controller = new AbortController();
    update({ busy: true, error: null, notice: null, profileError: null });
    try {
      let mutationError;
      let acknowledged = false;
      if (!checkOnly) {
        try { await cancelJob({ signal: controller.signal }); acknowledged = true; }
        catch (error) { mutationError = error; }
      }
      if (active && current === epoch) await reconcile(mutationError, acknowledged, current);
    } finally {
      if (active && current === epoch) update({ busy: false });
    }
  }

  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getSnapshot: () => state,
    cancel: () => run(false),
    checkStatus: () => run(true),
    start() { active = true; },
    stop() { active = false; epoch += 1; controller?.abort(); update({ busy: false }); },
  };
}
