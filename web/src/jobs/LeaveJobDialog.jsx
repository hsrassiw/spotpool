import { useEffect, useRef } from 'react';
import { useBlocker } from 'react-router';
import { Button } from '../components/Button.jsx';

export default function LeaveJobDialog({ when }) {
  const blocker = useBlocker(when);
  const ref = useRef(null);
  useEffect(() => {
    if (blocker.state === 'blocked' && !ref.current.open) ref.current.showModal();
    else if (blocker.state !== 'blocked' && ref.current.open) ref.current.close();
  }, [blocker.state]);
  useEffect(() => {
    if (!when) return;
    const warn = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [when]);
  return (
    <dialog className="confirm-dialog" ref={ref} aria-labelledby="leave-job-title" aria-describedby="leave-job-description"
      onCancel={(event) => { event.preventDefault(); blocker.reset?.(); }}>
      <h2 id="leave-job-title">Rời trang tạo job?</h2>
      <p id="leave-job-description">File và link tải lên chỉ được giữ trong trang này. Rời trang sẽ dừng upload và mất dữ liệu để tiếp tục. Yêu cầu đã gửi có thể vẫn được server xử lý; job không tự bị hủy.</p>
      <div className="create-actions"><Button variant="secondary" onClick={() => blocker.reset()}>Ở lại</Button><Button onClick={() => blocker.proceed()}>Rời trang</Button></div>
    </dialog>
  );
}
