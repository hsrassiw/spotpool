import { ApiError } from './client.js';

const numericFields = ['creditAvailable', 'creditHeld', 'runningJobs', 'maxConcurrentJobs', 'createdAt'];

export function parseProfile(value) {
  if (!value || typeof value.userId !== 'string' || !value.userId
    || typeof value.email !== 'string' || !['user', 'admin'].includes(value.role)
    || numericFields.some((field) => !Number.isSafeInteger(value[field]) || value[field] < 0)) {
    throw new ApiError('Thông tin tài khoản từ API chưa đúng hợp đồng. Vui lòng báo nhóm kiểm tra.', { code: 'INVALID_RESPONSE' });
  }
  // Chỉ dùng dữ liệu hồ sơ công khai, không giữ các trường lạ từ response.
  return Object.freeze(Object.fromEntries(['userId', 'email', 'role', ...numericFields].map((field) => [field, value[field]])));
}

export function createUsersApi(client) {
  return {
    async getCurrentUser(apiKey, { signal } = {}) {
      return parseProfile(await client.request('/users/me', { apiKey, signal }));
    },
    async registerUser(email, { signal } = {}) {
      const value = await client.request('/users', { method: 'POST', body: { email }, signal });
      if (!value || typeof value.userId !== 'string' || !value.userId || typeof value.apiKey !== 'string' || !value.apiKey) {
        throw new ApiError('Tài khoản có thể đã tạo nhưng response thiếu thông tin khóa. Không gửi lại ngay; hãy báo nhóm kiểm tra.', { code: 'INVALID_RESPONSE' });
      }
      return { userId: value.userId, email: typeof value.email === 'string' ? value.email : email, apiKey: value.apiKey };
    },
  };
}
