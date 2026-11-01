const statuses = {
  UPLOADING: { label: 'Chờ tải file', tone: 'neutral' },
  QUEUED: { label: 'Đang chờ', tone: 'neutral' },
  RUNNING: { label: 'Đang chạy', tone: 'primary' },
  PAUSED: { label: 'Tạm dừng', tone: 'paused' },
  SUCCEEDED: { label: 'Hoàn thành', tone: 'success' },
  FAILED: { label: 'Thất bại', tone: 'danger' },
  CANCELLED: { label: 'Đã hủy', tone: 'danger' },
};

export const statusOptions = Object.entries(statuses).map(([value, { label }]) => ({ value, label }));

export default function StatusBadge({ status }) {
  const { label, tone } = statuses[status] ?? { label: 'Chưa xác định', tone: 'neutral' };
  return <span className={`badge badge--${tone}`}><span className="badge-dot" />{label}</span>;
}
