export function safeReturnPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return '/jobs';
  const url = new URL(value, 'https://spotpool.invalid');
  if (url.origin !== 'https://spotpool.invalid' || ['/login', '/register'].includes(url.pathname)) return '/jobs';
  return url.pathname + url.search + url.hash;
}
