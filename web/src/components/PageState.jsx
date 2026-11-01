import Icon from './Icon.jsx';
import { Button } from './Button.jsx';

export function EmptyState({ title = 'Chưa có dữ liệu', description, icon = 'file', children }) {
  return (
    <div className="page-state">
      <span className="state-icon"><Icon name={icon} size={30} /></span>
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {children && <div className="state-actions">{children}</div>}
    </div>
  );
}

export function LoadingState({ message = 'Đang tải dữ liệu…' }) {
  return <div className="page-state" role="status"><span className="spinner" aria-hidden="true" /><p>{message}</p></div>;
}

export function ErrorState({ message = 'Chưa tải được dữ liệu. Vui lòng thử lại.', onRetry }) {
  return (
    <div className="page-state" role="alert">
      <span className="state-icon state-icon--error"><Icon name="alert" size={30} /></span>
      <h2>Không thể tải dữ liệu</h2><p>{message}</p>
      {onRetry && <Button icon="refresh" variant="secondary" onClick={onRetry}>Thử lại</Button>}
    </div>
  );
}
