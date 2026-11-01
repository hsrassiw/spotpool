import { createApiClient } from './client.js';
import { createUsersApi } from './users.js';

export const apiClient = createApiClient({ baseUrl: import.meta.env.VITE_API_BASE_URL });
export const usersApi = createUsersApi(apiClient);
