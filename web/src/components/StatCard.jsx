import Icon from './Icon.jsx';

export default function StatCard({ title, value, icon, children, tone = 'primary' }) {
  return (
    <article className="stat-card">
      <span className={`stat-icon stat-icon--${tone}`}><Icon name={icon} size={26} /></span>
      <div><h2 className="stat-title">{title}</h2><p className="stat-value">{value}</p>{children && <p className="stat-note">{children}</p>}</div>
    </article>
  );
}
