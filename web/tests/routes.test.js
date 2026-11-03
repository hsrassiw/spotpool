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
const profile = { userId: 'test-user', email: 'real-profile@example.com', role: 'user', creditAvailable: 12345,
  creditHeld: 0, runningJobs: 0, maxConcurrentJobs: 2, createdAt: 1793491200 };

before(async () => {
  server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  ({ default: App } = await server.ssrLoadModule('/src/app/App.jsx'));
  ({ AuthProvider } = await server.ssrLoadModule('/src/auth/AuthContext.jsx'));
  ({ createSessionStore } = await server.ssrLoadModule('/src/auth/sessionStore.js'));
  ({ CreateJobView } = await server.ssrLoadModule('/src/pages/CreateJobPage.jsx'));
  ({ createJobStore } = await server.ssrLoadModule('/src/jobs/createJobStore.js'));
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
  assert.match(detail, /Trang chi tiết đang được xây dựng/);
});

test('user cannot render admin page; admin gets the route and navigation item', async () => {
  const user = createSessionStore({ loadProfile: async () => profile, storage: null });
  await user.signIn('fake-user-key');
  assert.match(render(user, '/admin'), /Bạn không có quyền quản trị/);
  const admin = createSessionStore({ loadProfile: async () => ({ ...profile, role: 'admin' }), storage: null });
  await admin.signIn('fake-admin-key');
  assert.match(render(admin, '/admin'), /href="\/admin"/);
  assert.match(render(admin, '/admin'), /Không gian quản trị/);
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
