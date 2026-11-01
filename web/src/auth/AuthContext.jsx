import { createContext, useContext, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { apiClient, usersApi } from '../api/index.js';
import { createSessionStore } from './sessionStore.js';

const AuthContext = createContext(null);

export function AuthProvider({ children, store: suppliedStore }) {
  const [store] = useState(() => suppliedStore ?? createSessionStore({ loadProfile: usersApi.getCurrentUser }));
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  // Giữ cùng hàm request khi chỉ làm mới số dư; danh sách không bị tải lại vô cớ.
  const request = useCallback((path, options = {}) => apiClient.request(path, {
    ...options, apiKey: store.getApiKey(), sessionSignal: store.getSessionSignal(),
  }), [store]);

  useEffect(() => {
    store.restore().catch(() => { /* Lỗi đã được đưa vào state để UI xử lý. */ });
    return () => store.cancelPending();
  }, [store]);

  const value = useMemo(() => ({
    ...state,
    signIn: store.signIn,
    signOut: () => store.signOut(),
    restore: store.restore,
    refreshProfile: store.refreshProfile,
    cancelSignIn: store.cancelPending,
    request,
  }), [state, store, request]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
