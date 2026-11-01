const numberFormatter = new Intl.NumberFormat('vi-VN');
const dateFormatter = new Intl.DateTimeFormat('vi-VN', {
  day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'Asia/Ho_Chi_Minh',
});

export function formatCredit(value) {
  return value == null ? '—' : numberFormatter.format(value);
}

export function formatDate(epochSeconds) {
  return epochSeconds == null ? '—' : dateFormatter.format(new Date(epochSeconds * 1000));
}

export function tierLabel(tier) {
  return ({ ondemand: 'Ổn định', spot: 'Tiết kiệm' })[tier] ?? 'Chưa xác định';
}

export function kindLabel(kind) {
  return ({ video: 'Video', script: 'Python' })[kind] ?? 'Chưa xác định';
}
