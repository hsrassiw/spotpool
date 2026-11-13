export const MAX_FILES = 100;
// Khớp MAX_INPUT_BYTES ở services/api/start_job.py: máy worker chạy 2 suất trên ổ đĩa chung.
export const MAX_FILE_BYTES = 500 * 1024 * 1024;

export function validateJobDraft(draft, files) {
  const errors = {};
  if (!['script', 'video'].includes(draft.kind)) errors.kind = 'Chọn loại tác vụ hợp lệ.';
  if (!['spot', 'ondemand'].includes(draft.tier)) errors.tier = 'Chọn gói xử lý hợp lệ.';
  const cap = String(draft.costCap).trim();
  if (!/^\d+$/.test(cap) || !Number.isSafeInteger(Number(cap)) || Number(cap) <= 0) errors.costCap = 'Trần chi phí phải là số credit nguyên dương trong khoảng an toàn.';
  if (!Array.isArray(files) || files.length < 1 || files.length > MAX_FILES) errors.files = 'Chọn từ 1 đến 100 file.';
  else if (files.some((file) => typeof file.name !== 'string' || !file.name.trim() || !Number.isSafeInteger(file.size) || file.size < 0)) errors.files = 'Có file không hợp lệ. Hãy chọn lại từ máy.';
  else if (files.some((file) => file.size > MAX_FILE_BYTES)) errors.files = `Mỗi file tối đa 500 MB. File quá lớn: ${files.filter((file) => file.size > MAX_FILE_BYTES).map((file) => file.name).join(', ')}.`;
  else if (draft.kind === 'script' && files.some((file) => !/\.(py|zip)$/i.test(file.name))) errors.files = 'Job Python nhận file .py hoặc .zip (có __main__.py ở gốc); chưa hỗ trợ cài thư viện qua mạng.';
  else if (draft.kind === 'video' && files.some((file) => !/^video\//i.test(file.type || '') && !/\.(mp4|mov|mkv|avi|webm|m4v|mpeg|mpg|ogg)$/i.test(file.name))) errors.files = 'Hãy chọn file video cho loại tác vụ này.';
  return {
    errors,
    payload: Object.keys(errors).length ? null : {
      tier: draft.tier, kind: draft.kind, vcpu: 1, costCap: Number(cap), files: files.map((file) => file.name), params: {},
    },
  };
}

export function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${new Intl.NumberFormat('vi-VN', { maximumFractionDigits: index ? 1 : 0 }).format(bytes / 1024 ** index)} ${units[index]}`;
}
