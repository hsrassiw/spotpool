import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router';
import { Button } from '../components/Button.jsx';
import Icon from '../components/Icon.jsx';
import Logo from '../components/Logo.jsx';
import { LoadingState } from '../components/PageState.jsx';
import { usersApi } from '../api/index.js';
import { useAuth } from '../auth/AuthContext.jsx';
import { safeReturnPath } from '../auth/redirect.js';

export default function AccessPage({ register = false }) {
  const auth = useAuth();
  const { cancelSignIn } = auth;
  const location = useLocation();
  const navigate = useNavigate();
  const registrationController = useRef(null);
  const signingIn = useRef(false);
  const [email, setEmail] = useState('');
  const [key, setKey] = useState('');
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [createdUser, setCreatedUser] = useState(null);
  const [saved, setSaved] = useState(false);
  const [copyMessage, setCopyMessage] = useState('');
  const [unknownOutcome, setUnknownOutcome] = useState(false);
  const title = createdUser ? 'Lưu khóa API của bạn' : register ? 'Tạo tài khoản SpotPool' : 'Truy cập SpotPool';
  const destination = safeReturnPath(location.state?.from);

  useEffect(() => { document.title = `${title} · SpotPool`; }, [title]);
  useEffect(() => () => {
    registrationController.current?.abort();
    // Không hủy restore của Provider khi chỉ đổi route lúc đang kiểm tra phiên.
    if (signingIn.current) cancelSignIn();
  }, [cancelSignIn]);
  useEffect(() => {
    if (!createdUser || saved) return;
    function warnBeforeLeaving(event) { event.preventDefault(); event.returnValue = ''; }
    window.addEventListener('beforeunload', warnBeforeLeaving);
    return () => window.removeEventListener('beforeunload', warnBeforeLeaving);
  }, [createdUser, saved]);

  async function submit(event) {
    event.preventDefault();
    if (busy || unknownOutcome) return;
    setBusy(true);
    setError(null);
    try {
      if (register) {
        registrationController.current = new AbortController();
        const user = await usersApi.registerUser(email.trim(), { signal: registrationController.current.signal });
        setCreatedUser(user);
        setVisible(false);
      } else {
        signingIn.current = true;
        await auth.signIn(key);
        setKey('');
        navigate(destination, { replace: true });
      }
    } catch (failure) {
      if (failure.name === 'AbortError') return;
      setError(failure.message);
      if (register && ['NETWORK', 'TIMEOUT', 'INVALID_RESPONSE'].includes(failure.code)) setUnknownOutcome(true);
    } finally { signingIn.current = false; setBusy(false); }
  }

  async function continueWithNewKey() {
    if (!saved || busy) return;
    setBusy(true);
    setError(null);
    signingIn.current = true;
    try {
      await auth.signIn(createdUser.apiKey);
      navigate(destination, { replace: true });
    } catch (failure) {
      if (failure.name !== 'AbortError') setError(failure.message);
    } finally { signingIn.current = false; setBusy(false); }
  }

  async function copyKey() {
    try {
      await navigator.clipboard.writeText(createdUser.apiKey);
      setCopyMessage('Đã sao chép khóa. Hãy lưu ở nơi riêng tư.');
    } catch { setCopyMessage('Không sao chép được. Hãy hiện khóa, chọn và lưu thủ công.'); }
  }

  if (auth.status === 'checking') return <main className="access-page"><LoadingState message="Đang kiểm tra phiên truy cập…" /></main>;
  if (auth.status === 'authenticated') return <Navigate to={destination} replace />;

  return (
    <main className="access-page">
      <div className="access-card panel">
        {createdUser ? <Logo /> : <Link to="/login" className="brand-link"><Logo /></Link>}
        <span className="state-icon"><Icon name={createdUser ? 'check' : 'user'} size={28} /></span>
        <h1>{title}</h1>
        <p className="page-description">{createdUser ? 'Khóa chỉ được hiển thị sau khi tạo. Hãy sao chép và lưu trước khi tiếp tục.'
          : register ? 'Tạo tài khoản và nhận khóa API để bắt đầu.' : 'Nhập khóa API để truy cập tài khoản của bạn.'}</p>

        {!createdUser && <form className="access-form" onSubmit={submit}>
          {register ? <div className="form-field">
            <label htmlFor="email">Email <span className="field-optional">(không bắt buộc)</span></label>
            <input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)}
              autoComplete="email" placeholder="ban@example.com" disabled={busy || unknownOutcome} />
          </div> : <div className="form-field">
            <label htmlFor="api-key">Khóa API</label>
            <div className="key-input"><input id="api-key" type={visible ? 'text' : 'password'} value={key}
              onChange={(event) => setKey(event.target.value)} required autoComplete="off" spellCheck={false}
              autoCapitalize="none" placeholder="Nhập khóa của bạn" disabled={busy} />
              <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? 'Ẩn khóa API' : 'Hiện khóa API'}>{visible ? 'Ẩn' : 'Hiện'}</button>
            </div>
          </div>}
          {(error || (!register && auth.error)) && <p className="form-error" role="alert">{error || auth.error}</p>}
          {unknownOutcome && <div className="notice notice--warning"><p>Kết quả tạo tài khoản chưa rõ. Gửi lại có thể tạo thêm một tài khoản. Hãy kiểm tra với nhóm trước.</p></div>}
          <Button type="submit" disabled={busy || unknownOutcome || (!register && !key.trim())}>
            {busy ? 'Đang xử lý…' : register ? 'Tạo tài khoản' : 'Truy cập'}
          </Button>
          {unknownOutcome && <Button variant="secondary" onClick={() => setUnknownOutcome(false)}>Tôi đã kiểm tra, cho phép gửi lại</Button>}
        </form>}

        {createdUser && <div className="access-form">
          <dl className="key-values compact-values"><div><dt>Mã tài khoản</dt><dd className="mono">{createdUser.userId}</dd></div></dl>
          <div className="form-field"><label htmlFor="new-api-key">Khóa API vừa tạo</label>
            <div className="key-input"><input id="new-api-key" className="mono" readOnly value={createdUser.apiKey}
              type={visible ? 'text' : 'password'} autoComplete="off" spellCheck={false} />
              <button type="button" onClick={() => setVisible(!visible)}>{visible ? 'Ẩn' : 'Hiện'}</button></div>
          </div>
          <Button variant="secondary" onClick={copyKey}>Sao chép khóa</Button>
          {copyMessage && <p className="form-help" role="status">{copyMessage}</p>}
          <label className="checkbox-field"><input type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} />Tôi đã lưu khóa API ở nơi riêng tư.</label>
          {error && <p className="form-error" role="alert">{error} Khóa vừa tạo vẫn được giữ trên màn hình này.</p>}
          <Button disabled={!saved || busy} onClick={continueWithNewKey}>{busy ? 'Đang kiểm tra tài khoản…' : 'Tiếp tục vào SpotPool'}</Button>
        </div>}

        {!createdUser && <Link className="text-link" to={register ? '/login' : '/register'} state={{ from: destination }}>
          {register ? 'Đã có tài khoản? Truy cập' : 'Chưa có tài khoản? Đăng ký'}
        </Link>}
      </div>
    </main>
  );
}
