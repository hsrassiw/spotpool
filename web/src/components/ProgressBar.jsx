export default function ProgressBar({ completed, total, label = 'Tiến độ', tone = 'primary' }) {
  const percent = total > 0 ? Math.min(100, Math.max(0, Math.round(completed / total * 100))) : 0;
  return (
    <div className="progress-cell">
      <div className={`progress-track progress-track--${tone}`} role="progressbar"
        aria-label={label} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}
        aria-valuetext={`${completed} trên ${total} task`}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <span className="progress-count">{completed}/{total}</span>
    </div>
  );
}
