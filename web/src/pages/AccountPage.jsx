import { useState } from 'react';
import { Button } from '../components/Button.jsx';
import PageHeader from '../components/PageHeader.jsx';
import { useAuth } from '../auth/AuthContext.jsx';
import { formatCredit, formatDate } from '../utils/format.js';

export default function AccountPage() {
  const { profile, refreshProfile, signOut, storageAvailable } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function refresh() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try { await refreshProfile(); }
    catch (failure) { if (failure.name !== 'AbortError') setError(failure.message); }
    finally { setBusy(false); }
  }

  return (
    <>
      <PageHeader title="Tài khoản" description="Thông tin tài khoản và credit của bạn."
        action={<Button icon="refresh" variant="secondary" onClick={refresh} disabled={busy}>{busy ? 'Đang tải…' : 'Làm mới'}</Button>} />
      {error && <p className="form-error" role="alert">{error}</p>}
      {!storageAvailable && <div className="notice notice--warning"><p>Trình duyệt không lưu được phiên. Bạn có thể cần nhập lại khóa khi tải lại trang.</p></div>}
      <section className="panel account-panel">
        <div className="section-heading"><h2>Thông tin của bạn</h2><span className="badge badge--primary">{profile.role === 'admin' ? 'Quản trị viên' : 'Người dùng'}</span></div>
        <dl className="key-values">
          <div><dt>Email</dt><dd>{profile.email || 'Chưa cung cấp'}</dd></div>
          <div><dt>Mã người dùng</dt><dd className="mono">{profile.userId}</dd></div>
          <div><dt>Credit khả dụng</dt><dd>{formatCredit(profile.creditAvailable)} credit</dd></div>
          <div><dt>Credit đang giữ</dt><dd>{formatCredit(profile.creditHeld)} credit</dd></div>
          <div><dt>Job đang sử dụng hạn mức</dt><dd>{profile.runningJobs} / {profile.maxConcurrentJobs}</dd></div>
          <div><dt>Ngày tạo</dt><dd>{formatDate(profile.createdAt)}</dd></div>
        </dl>
        <div className="account-actions"><p>Kết thúc phiên chỉ xóa khóa trên trình duyệt này, không hủy các job đang chạy.</p><Button variant="secondary" onClick={signOut}>Kết thúc phiên</Button></div>
      </section>
    </>
  );
}
