const STORAGE_KEY = 'spotpool.apiKey';

function browserStorage() {
  try { return typeof window === 'undefined' ? null : window.sessionStorage; } catch { return null; }
}

function cancelled() {
  return new DOMException('Authentication cancelled', 'AbortError');
}

export function createSessionStore({ loadProfile, storage = browserStorage() }) {
  let state = Object.freeze({ status: 'checking', profile: null, error: null, storageAvailable: Boolean(storage) });
  let apiKey = '';
  let rememberedKey = '';
  let operation = 0;
  let authController = null;
  let sessionController = new AbortController();
  let storageAvailable = Boolean(storage);
  const listeners = new Set();

  function publish(next) {
    state = Object.freeze({ ...next, storageAvailable });
    for (const listener of listeners) listener();
  }

  function readStoredKey() {
    try { return storage?.getItem(STORAGE_KEY)?.trim() || ''; } catch { storageAvailable = false; return ''; }
  }

  function remember(key) {
    rememberedKey = key;
    try {
      if (key) storage?.setItem(STORAGE_KEY, key);
      else storage?.removeItem(STORAGE_KEY);
    } catch { storageAvailable = false; }
  }

  function cancelPending() {
    operation += 1;
    authController?.abort();
    authController = null;
  }

  function signOut(error = null) {
    cancelPending();
    sessionController.abort();
    sessionController = new AbortController();
    apiKey = '';
    remember('');
    publish({ status: 'anonymous', profile: null, error });
  }

  async function authenticate(candidate, mode) {
    cancelPending();
    const currentOperation = operation;
    const controller = new AbortController();
    authController = controller;
    const previous = state;
    if (mode === 'restore') publish({ status: 'checking', profile: null, error: null });
    else if (mode === 'login' && previous.status !== 'authenticated') publish({ status: 'anonymous', profile: null, error: null });

    try {
      const profile = await loadProfile(candidate, { signal: controller.signal });
      if (currentOperation !== operation || controller.signal.aborted) throw cancelled();
      if (candidate !== apiKey || previous.status !== 'authenticated') {
        sessionController.abort();
        sessionController = new AbortController();
      }
      apiKey = candidate;
      remember(candidate);
      publish({ status: 'authenticated', profile, error: null });
      return profile;
    } catch (error) {
      if (currentOperation !== operation || controller.signal.aborted) throw cancelled();
      const denied = error.status === 401 || error.status === 403;
      if (mode === 'refresh' && denied) signOut(error.message);
      else if (mode === 'restore') {
        if (denied) { apiKey = ''; remember(''); }
        publish({ status: denied ? 'anonymous' : 'error', profile: null, error: error.message });
      } else if (previous.status !== 'authenticated') {
        publish({ status: 'anonymous', profile: null, error: error.message });
      }
      throw error;
    } finally {
      if (authController === controller) authController = null;
    }
  }

  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    getApiKey: () => apiKey,
    getSessionSignal: () => sessionController.signal,
    cancelPending,
    signOut,
    async restore() {
      const key = rememberedKey || readStoredKey();
      if (!key) { signOut(); return null; }
      return authenticate(key, 'restore');
    },
    async signIn(value) {
      const key = typeof value === 'string' ? value.trim() : '';
      if (!key) throw new Error('Vui lòng nhập khóa API.');
      return authenticate(key, 'login');
    },
    async refreshProfile() {
      if (!apiKey) throw new Error('Phiên truy cập chưa được xác thực.');
      return authenticate(apiKey, 'refresh');
    },
  };
}
