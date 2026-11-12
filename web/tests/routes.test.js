import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { createServer } from 'vite';

let server;
let App;
let AuthProvider;
let createSessionStore;
let CreateJobView;
let createJobStore;
let JobDetailView;
const profile = { userId: 'test-user', email: 'real-profile@example.com', role: 'user', creditAvailable: 12345,
  creditHeld: 0, runningJobs: 0, maxConcurrentJobs: 2, createdAt: 1793491200 };

before(async () => {
  server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ default: App } = await server.ssrLoadModule('/src/app/App.jsx'));
  ({ AuthProvider } = await server.ssrLoadModule('/src/auth/AuthContext.jsx'));
  ({ createSessionStore } = await server.ssrLoadModule('/src/auth/sessionStore.js'));
  ({ CreateJobView } = await server.ssrLoadModule('/src/pages/CreateJobPage.jsx'));
  ({ createJobStore } = await server.ssrLoadModule('/src/jobs/createJobStore.js'));
  ({ JobDetailView } = await server.ssrLoadModule('/src/pages/JobDetailPage.jsx'));
});
after(async () => { await server?.close(); });

function render(store, route) {
  const router = createMemoryRouter([{ path: '*', element: createElement(AuthProvider, { store }, createElement(App)) }], { initialEntries: [route] });
  try { return renderToStaticMarkup(createElement(RouterProvider, { router })); }
  finally { router.dispose(); }
}

test('anonymous users get a real login/register form, not private account content', async () => {
  const store = createSessionStore({ loadProfile: async () => profile, storage: null });
  await store.restore();
  const login = render(store, '/login');
  assert.match(login, /id="api-key"/);
  assert.match(login, /type="password"/);
  assert.match(render(store, '/register'), /id="email"/);
  assert.equal(render(store, '/account').includes(profile.email), false);
});

test('authenticated layout uses real profile and does not render the saved key', async () => {
  const store = createSessionStore({ loadProfile: async () => profile, storage: null });
  await store.signIn('fake-secret-key');
  const account = render(store, '/account');
  assert.match(account, /real-profile@example.com/);
  assert.match(account, /12\.345/);
  assert.equal(account.includes('fake-secret-key'), false);
  assert.equal(account.includes('khue@example.com'), false);
  assert.equal(account.includes('href="/admin"'), false);
  const jobs = render(store, '/jobs');
  assert.equal((jobs.match(/class="job-name"/g) || []).length, 0);
  assert.match(jobs, /Đang tải danh sách job/);
  assert.equal(jobs.includes('Dữ liệu job minh họa'), false);
  const detail = render(store, '/jobs/real-job-id');
  assert.match(detail, /real-job-id/);
  assert.match(detail, /Đang tải chi tiết job/);
});

test('user cannot render admin page; admin gets the route and navigation item', async () => {
  const user = createSessionStore({ loadProfile: async () => profile, storage: null });
  await user.signIn('fake-user-key');
  assert.match(render(user, '/admin'), /Bạn không có quyền quản trị/);
  const admin = createSessionStore({ loadProfile: async () => ({ ...profile, role: 'admin' }), storage: null });
  await admin.signIn('fake-admin-key');
  assert.match(render(admin, '/admin'), /href="\/admin"/);
  assert.match(render(admin, '/admin'), /Tổng quan quản trị/);
});

test('transient restore failure renders retry screen without leaking account data', async () => {
  const storage = { getItem: () => 'saved-fake-key', removeItem() {} };
  const store = createSessionStore({ storage, loadProfile: async () => { throw new Error('Controlled network error'); } });
  await assert.rejects(store.restore());
  const html = render(store, '/jobs');
  assert.match(html, /Controlled network error/);
  assert.equal(html.includes('saved-fake-key'), false);
  assert.equal(html.includes('class="job-name"'), false);
});

test('create route renders actual file/config form inside the data router and still requires authentication', async () => {
  const store = createSessionStore({ loadProfile: async () => profile, storage: null });
  await store.signIn('fake-secret-key');
  const html = render(store, '/jobs/new');
  assert.match(html, /id="job-files"/);
  assert.match(html, /type="file"/);
  assert.match(html, /id="job-cap"/);
  assert.match(html, /Tạo job và tải file/);
  assert.equal(html.includes('Khu vực tạo job đang được chuẩn bị'), false);
  assert.equal(html.includes('fake-secret-key'), false);
  store.signOut();
  assert.equal(render(store, '/jobs/new').includes('id="job-files"'), false);
});

test('restart warning is shown only for Python scripts on the savings plan', () => {
  const model = createJobStore({ api: {}, uploadFile: async () => {} });
  const warning = 'Script chạy lâu ở gói Tiết kiệm: nếu máy bị thu hồi thì script chạy lại từ đầu, trừ khi script lưu trạng thái vào thư mục <code>SPOTPOOL_CHECKPOINT_DIR</code> để chạy tiếp từ đó.';
  for (const [kind, tier] of [['script', 'spot'], ['script', 'ondemand'], ['video', 'spot'], ['video', 'ondemand'], ['script', 'spot']]) {
    model.configure({ kind, tier });
    const router = createMemoryRouter([{ path: '/', element: createElement(CreateJobView, { state: model.getSnapshot(), actions: model, profile }) }]);
    try {
      const html = renderToStaticMarkup(createElement(RouterProvider, { router }));
      assert.equal(html.includes(warning), kind === 'script' && tier === 'spot', `${kind}/${tier}`);
    } finally { router.dispose(); }
  }
});

function detailView(job, fileState = { log: null, download: null }, cancelState = {}) {
  const router = createMemoryRouter([{ path: '/', element: createElement(JobDetailView, { jobId: 'job-1',
    state: { job, loading: false, error: null, updatedAt: 1793491200000 }, fileState, cancelState, cancelAction: { cancel() {}, checkStatus() {} }, refresh() {}, files: { closeLog() {} } }) }]);
  try { return renderToStaticMarkup(createElement(RouterProvider, { router })); }
  finally { router.dispose(); }
}

const detailJob = () => ({ jobId: 'job-1', status: 'RUNNING', interrupted: true, kind: 'script', tier: 'spot', vcpu: 1,
  costCap: 5000, usedVcpuSeconds: 10, chargedCredit: null, progress: { total: 2, succeeded: 1, failed: 0 },
  createdAt: 1793491200, queuedAt: 1793491201, startedAt: null, finishedAt: null,
  tasks: [{ taskId: 'task-0001', fileName: '<img src=x onerror=alert(1)>.py', status: 'SUCCEEDED', interruptCount: 1,
    logUrl: 'https://output.s3.amazonaws.com/log?Signature=private-log', resultUrl: 'https://output.s3.amazonaws.com/result?Signature=private-result' },
  { taskId: 'task-0002', fileName: 'pending.py', status: 'RUNNING', interruptCount: 3, logUrl: null, resultUrl: null }] });

test('detail view shows actual progress, gates file actions and escapes filenames without exposing unused signed URLs', () => {
  const html = detailView(detailJob());
  assert.match(html, /Bị gián đoạn, đang chạy tiếp/);
  assert.match(html, /task-0002/);
  assert.match(html, /&lt;img/);
  assert.equal(html.includes('<img src=x'), false);
  assert.equal((html.match(/Tải kết quả/g) || []).length, 1);
  assert.equal((html.match(/Xem log/g) || []).length, 1);
  assert.equal(html.includes('private-result'), false);
  assert.equal(html.includes('private-log'), false);
  assert.equal(html.includes('máy On-Demand'), false);
  assert.equal(detailView({ ...detailJob(), status: 'PAUSED' }).includes('Bị gián đoạn, đang chạy tiếp'), false);
  assert.match(detailView({ ...detailJob(), tasks: [] }), /Đang chuẩn bị các task/);
  assert.match(detailView({ ...detailJob(), status: 'CANCELLED', queuedAt: null, startedAt: null, interrupted: false }), /Chưa chạy/);
});

test('script jobs show when the latest checkpoint was saved; video jobs have no checkpoint column', () => {
  const job = detailJob();
  const saved = { ...job, tasks: [{ ...job.tasks[0], checkpointAt: 1793491300 }, { ...job.tasks[1], checkpointAt: null }] };
  const html = detailView(saved);
  assert.match(html, /<th scope="col">Checkpoint<\/th>/);
  assert.match(html, /Lưu lúc/);
  assert.match(html, /Chưa lưu/);
  assert.match(html, /chạy tiếp từ checkpoint gần nhất/);
  const video = detailView({ ...saved, kind: 'video' });
  assert.equal(video.includes('Checkpoint</th>'), false);
  assert.match(video, /Task bị gián đoạn sẽ chạy lại từ đầu/);
});

test('log content renders as escaped text with an explicit truncation notice and full-log link', () => {
  const html = detailView(detailJob(), { download: null, log: { taskId: 'task-0001', fileName: 'test.py', loading: false,
    text: '<script>window.secret=1</script>', truncated: true, url: 'https://output.s3.amazonaws.com/log?Signature=opened-log' } });
  assert.match(html, /&lt;script&gt;window.secret=1&lt;\/script&gt;/);
  assert.equal(html.includes('<script>window.secret'), false);
  assert.match(html, /1 MiB/);
  assert.match(html, /Mở log đầy đủ/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test('detail cancellation is available only for active jobs and unknown outcomes require a status check', () => {
  assert.match(detailView(detailJob()), />Hủy job</);
  assert.equal(detailView({ ...detailJob(), status: 'SUCCEEDED' }).includes('>Hủy job<'), false);
  const html = detailView(detailJob(), { log: null, download: null }, { unknown: true, error: 'Chưa xác nhận được trạng thái.' });
  assert.match(html, /disabled=""[^>]*>.*?Hủy job/s);
  assert.match(html, /Kiểm tra trạng thái/);
});

test('upload view never exposes signed URLs; run remains disabled until all uploads and credit confirmation', async () => {
  const jobId = '22222222-2222-4222-8222-222222222222';
  let settleUpload;
  const uploading = new Promise((resolve) => { settleUpload = resolve; });
  const model = createJobStore({ api: { createJob: async () => ({ jobId, uploads: [{ taskId: 'task-0001', url: 'https://input.s3.ap-southeast-1.amazonaws.com/a?Signature=private-test', expiresIn: 3600 }] }) }, uploadFile: () => uploading });
  model.setFiles([new File(['print(1)'], 'test.py')]);
  const creating = model.create();
  await new Promise((resolve) => setImmediate(resolve));
  function view() {
    const router = createMemoryRouter([{ path: '/', element: createElement(CreateJobView, { state: model.getSnapshot(), actions: model, profile }) }]);
    try { return renderToStaticMarkup(createElement(RouterProvider, { router })); }
    finally { router.dispose(); }
  }
  const pending = view();
  assert.match(pending, /Đang tải/);
  assert.equal(pending.includes('private-test'), false);
  const runButton = (html) => html.match(/<button\b[^>]*>(?:(?!<\/button>).)*Chạy job(?:(?!<\/button>).)*<\/button>/s)?.[0];
  assert.match(runButton(pending), /disabled=""/);
  settleUpload(); await creating;
  const ready = view();
  assert.match(ready, /Đã tải/);
  assert.match(runButton(ready), /disabled=""/); // Checkbox xác nhận credit chưa được chọn.
  assert.equal(ready.includes('Signature='), false);
});
