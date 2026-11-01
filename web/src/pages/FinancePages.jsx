import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '../components/Button.jsx';
import PageHeader from '../components/PageHeader.jsx';
import StatCard from '../components/StatCard.jsx';
import { EmptyState, ErrorState, LoadingState } from '../components/PageState.jsx';
import { useAuth } from '../auth/AuthContext.jsx';
import { formatCredit, formatDate } from '../utils/format.js';
import { createCreditRequestStore } from '../api/creditRequest.js';

function useReport(path) {
  const { request, profile } = useAuth();
  const userId = profile.userId;
  const [version, setVersion] = useState(0);
  const [state, setState] = useState({ userId, loading: true, data: null, error: null });
  useEffect(() => {
    const controller = new AbortController();
    setState({ userId, loading: true, data: null, error: null });
    request(path, { signal: controller.signal }).then(
      (data) => { if (!controller.signal.aborted) setState({ userId, loading: false, data, error: null }); },
      (error) => { if (!controller.signal.aborted) setState({ userId, loading: false, data: null, error: error.message }); },
    );
    return () => controller.abort();
  }, [path, request, version, userId]);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);
  return { ...(state.userId === userId ? state : { loading: true, data: null, error: null }), refresh };
}

function ReportState({ report, children }) {
  if (report.loading) return <LoadingState />;
  if (report.error) return <ErrorState message={report.error} onRetry={report.refresh} />;
  return children;
}

function CreditForm({ topup = false, onSuccess }) {
  const { request, refreshProfile, profile } = useAuth();
  const [requestStore] = useState(() => createCreditRequestStore(profile.userId, topup ? 'topup' : 'deposit'));
  const [restored] = useState(() => requestStore.load());
  const [amount, setAmount] = useState(String(restored?.amount ?? 5000));
  const [jobId, setJobId] = useState(restored?.jobId ?? '');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(Boolean(restored));
  const [message, setMessage] = useState(null);
  const attempt = useRef(restored);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function submit(event) {
    event.preventDefault();
    if (attempt.current?.busy) return;
    const value = Number(amount);
    if (!Number.isSafeInteger(value) || value < 1 || value > 100000000) return;
    attempt.current ??= { requestId: crypto.randomUUID(), amount: value, jobId: jobId.trim() };
    const current = attempt.current;
    requestStore.save(current);
    current.busy = true;
    setBusy(true);
    setMessage(null);
    try {
      await request(topup ? `/jobs/${encodeURIComponent(current.jobId)}/topup` : '/credits', {
        method: 'POST', body: { amount: current.amount, requestId: current.requestId },
      });
      attempt.current = null;
      requestStore.save(null);
      if (!mounted.current) return;
      setPending(false);
      setMessage({ ok: true, text: topup ? 'Đã tăng trần chi phí cho job.' : 'Đã nạp credit ảo.' });
      onSuccess();
      try { await refreshProfile(); }
      catch { if (mounted.current) setMessage({ ok: true, text: 'Giao dịch đã thành công. Hãy làm mới tài khoản để xem số dư.' }); }
    } catch (error) {
      if (!mounted.current) return;
      // Keep the original request after uncertain network/server results; retry is idempotent.
      const uncertain = !error.status || error.status >= 500;
      if (!uncertain) { attempt.current = null; requestStore.save(null); }
      setPending(uncertain);
      setMessage({ ok: false, text: uncertain ? `${error.message} Bấm Thử lại giao dịch để kiểm tra và hoàn tất cùng yêu cầu.` : error.message });
    } finally {
      current.busy = false;
      if (mounted.current) setBusy(false);
    }
  }
  const prefix = topup ? 'budget' : 'deposit';
  return <section className="panel finance-form">
    <h2>{topup ? 'Tăng trần chi phí job' : 'Nạp credit ảo'}</h2>
    <p>{topup ? 'Credit khả dụng sẽ được giữ thêm cho job. Job tạm dừng sẽ tiếp tục sau khi các task dừng xong.' : 'Credit dùng cho bài tập, không phát sinh thanh toán thật.'}</p>
    {restored && pending && <p role="status">Có giao dịch trước chưa rõ kết quả. Bấm Thử lại giao dịch để kiểm tra cùng yêu cầu.</p>}
    <form onSubmit={submit}>
      {topup && <label htmlFor={`${prefix}-job`}>Mã job<input id={`${prefix}-job`} value={jobId} onChange={(event) => setJobId(event.target.value)} required disabled={busy || pending} /></label>}
      <label htmlFor={`${prefix}-amount`}>Số credit<input id={`${prefix}-amount`} type="number" min="1" max="100000000" step="1" value={amount} onChange={(event) => setAmount(event.target.value)} required disabled={busy || pending} /></label>
      <Button type="submit" disabled={busy}>{busy ? 'Đang xử lý…' : pending ? 'Thử lại giao dịch' : topup ? 'Tăng trần' : 'Nạp credit'}</Button>
    </form>
    {message && <p role="status" className={message.ok ? 'finance-success' : 'form-error'}>{message.text}</p>}
  </section>;
}

export function BillingPage() {
  const report = useReport('/billing');
  const { profile } = useAuth();
  return <>
    <PageHeader title="Credit và hóa đơn" description="Số dư, ngân sách job và chi phí đã quyết toán."
      action={<Button variant="secondary" icon="refresh" onClick={report.refresh} disabled={report.loading}>Làm mới</Button>} />
    <div className="stats-grid">
      <StatCard title="Credit khả dụng" value={formatCredit(profile.creditAvailable)} icon="receipt" />
      <StatCard title="Đang giữ" value={formatCredit(profile.creditHeld)} icon="jobs" />
      <StatCard title="Job dùng hạn mức" value={`${profile.runningJobs} / ${profile.maxConcurrentJobs}`} icon="server" />
    </div>
    <div className="finance-grid"><CreditForm key={`deposit-${profile.userId}`} onSuccess={report.refresh} /><CreditForm key={`topup-${profile.userId}`} topup onSuccess={report.refresh} /></div>
    <ReportState report={report}>{report.data && <>
      <section className="panel finance-section"><h2>Mức dùng 7 ngày</h2><p>Theo ngày Việt Nam. Không gồm lượt bị gián đoạn do Spot.</p>
        <div className="finance-table-wrap"><table className="finance-table"><thead><tr><th>Ngày</th><th>vCPU-giây</th><th>Credit quyết toán</th></tr></thead>
          <tbody>{report.data.daily.map((day) => <tr key={day.date}><td>{day.date}</td><td>{formatCredit(day.vcpuSeconds)}</td><td>{formatCredit(day.chargedCredit)}</td></tr>)}</tbody></table></div>
      </section>
      <section className="panel finance-section"><h2>Hóa đơn theo job</h2>
        {!report.data.invoices.length ? <EmptyState title="Chưa có hóa đơn" description="Hóa đơn xuất hiện khi job đã được quyết toán." icon="receipt" /> :
          <div className="finance-table-wrap"><table className="finance-table"><thead><tr><th>Job</th><th>Gói</th><th>vCPU-giây tính phí</th><th>Đơn giá / vCPU-giờ</th><th>Credit</th><th>Quyết toán</th></tr></thead>
            <tbody>{report.data.invoices.map((invoice) => <tr key={invoice.jobId}><td className="mono">{invoice.jobId}</td><td>{invoice.tier === 'spot' ? 'Tiết kiệm' : 'Ổn định'}</td><td>{invoice.billableVcpuSeconds == null ? 'Chưa ghi nhận' : formatCredit(invoice.billableVcpuSeconds)}</td><td>{formatCredit(invoice.unitPrice)}</td><td>{formatCredit(invoice.chargedCredit)}</td><td>{formatDate(invoice.settledAt)}</td></tr>)}</tbody></table></div>}
      </section>
    </>}</ReportState>
  </>;
}

export function AdminPage() {
  const report = useReport('/admin/overview');
  const data = report.data;
  return <>
    <PageHeader title="Tổng quan quản trị" description="Tài nguyên xử lý, hàng đợi và chi phí vận hành."
      action={<Button variant="secondary" icon="refresh" onClick={report.refresh} disabled={report.loading}>Làm mới</Button>} />
    <ReportState report={report}>{data && <>
      <div className="stats-grid">
        <StatCard title="Máy đang chạy" value={data.machines.reduce((sum, row) => sum + row.count, 0)} icon="server" />
        <StatCard title="Task đang chờ" value={data.queues.ondemand.waiting + data.queues.spot.waiting} icon="jobs" />
        <StatCard title="Doanh thu 7 ngày" value={formatCredit(data.daily.reduce((sum, day) => sum + day.revenue, 0))} icon="receipt">credit ảo</StatCard>
      </div>
      <div className="finance-grid">{Object.entries(data.queues).map(([tier, queue]) => <section className="panel finance-section" key={tier}>
        <h2>{tier === 'spot' ? 'Tiết kiệm · Spot' : 'Ổn định · On-Demand'}</h2>
        <dl className="key-values"><div><dt>Task chờ</dt><dd>{queue.waiting}</dd></div><div><dt>Task đang nhận xử lý</dt><dd>{queue.running}</dd></div><div><dt>Tổng suất trên máy đang chạy</dt><dd>{queue.slots}</dd></div></dl>
        <p>Số liệu hàng đợi là ước lượng của SQS.</p>
      </section>)}</div>
      <section className="panel finance-section"><h2>Máy theo gói và vùng sẵn sàng</h2>
        {!data.machines.length ? <EmptyState title="Chưa có worker đang chạy" icon="server" /> : <div className="finance-table-wrap"><table className="finance-table"><thead><tr><th>Gói</th><th>Loại máy</th><th>AZ</th><th>Số máy</th></tr></thead><tbody>{data.machines.map((row) => <tr key={`${row.tier}-${row.instanceType}-${row.az}`}><td>{row.tier}</td><td>{row.instanceType}</td><td>{row.az}</td><td>{row.count}</td></tr>)}</tbody></table></div>}
      </section>
      <section className="panel finance-section"><h2>Doanh thu và chi phí AWS</h2>
        <p>Doanh thu theo ngày Việt Nam; AWS theo ngày UTC, có thể chậm một ngày. Quy đổi 1 USD = {formatCredit(data.usdToCredit)} credit để tham chiếu; credit không phải tiền thu thực tế.</p>
        <p>{data.awsCostUpdatedAt ? `Chi phí cập nhật: ${formatDate(data.awsCostUpdatedAt)}` : 'Chưa có dữ liệu chi phí AWS.'}</p>
        {data.awsCostWarning && <p className="form-error" role="status">Chi phí AWS chưa tải được hoặc đã cũ. Doanh thu vẫn hiển thị bình thường.</p>}
        <div className="finance-table-wrap"><table className="finance-table"><thead><tr><th>Ngày</th><th>Doanh thu (credit)</th><th>AWS (credit quy đổi)</th></tr></thead><tbody>{data.daily.map((day) => <tr key={day.date}><td>{day.date}</td><td>{formatCredit(day.revenue)}</td><td>{day.awsCost === null ? 'Chưa có' : formatCredit(day.awsCost)}</td></tr>)}</tbody></table></div>
      </section>
    </>}</ReportState>
  </>;
}
