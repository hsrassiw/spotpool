import { Link } from 'react-router';
import Icon from './Icon.jsx';

function content(icon, children) {
  return <>{icon && <Icon name={icon} size={18} />}<span>{children}</span></>;
}

export function Button({ children, icon, variant = 'primary', className = '', type = 'button', ...props }) {
  return <button type={type} className={`button button--${variant} ${className}`} {...props}>{content(icon, children)}</button>;
}

export function ButtonLink({ children, icon, variant = 'primary', className = '', to, ...props }) {
  return <Link to={to} className={`button button--${variant} ${className}`} {...props}>{content(icon, children)}</Link>;
}
