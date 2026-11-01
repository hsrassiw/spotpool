import { ApiError } from '../api/client.js';

// Một store thuộc đúng một tài khoản và một bộ lọc. Không lưu job vào storage.
export function createJobsStore({ loadPage, status = '' }) {
  let state = Object.freeze({ jobs: [], next: null, loading: true, loadingMore: false, hasLoaded: false, error: null, moreError: null });
  let epoch = 0;
  let controller;
  const listeners = new Set();
  const update = (values) => {
    state = Object.freeze({ ...state, ...values });
    listeners.forEach((listener) => listener());
  };
  const cancel = () => { epoch += 1; controller?.abort(); };

  async function load(more) {
    if (more && (state.loading || state.loadingMore || !state.next)) return;
    cancel();
    const current = epoch;
    controller = new AbortController();
    const cursor = more ? state.next : undefined;
    update(more ? { loadingMore: true, moreError: null } : { loading: true, loadingMore: false, error: null, moreError: null });
    try {
      const page = await loadPage({ status, next: cursor, signal: controller.signal });
      if (current !== epoch) return;
      if (cursor && page.next === cursor) throw new ApiError('API trả lại cùng trang. Vui lòng làm mới danh sách.', { code: 'INVALID_RESPONSE' });
      const unique = new Map((more ? state.jobs : []).map((job) => [job.jobId, job]));
      page.jobs.forEach((job) => unique.set(job.jobId, job));
      update({ jobs: [...unique.values()].sort((a, b) => b.createdAt - a.createdAt), next: page.next,
        hasLoaded: true, error: null, moreError: null });
    } catch (error) {
      if (current !== epoch) return;
      // Lỗi trang sau giữ nguyên danh sách/cursor để người dùng thử lại đúng trang.
      update(more ? { moreError: error } : { error });
    } finally {
      if (current === epoch) update({ loading: false, loadingMore: false });
    }
  }

  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getSnapshot: () => state,
    refresh: () => load(false),
    loadMore: () => load(true),
    cancel,
  };
}
