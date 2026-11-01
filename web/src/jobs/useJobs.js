import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { useAuth } from '../auth/AuthContext.jsx';
import { createJobsApi } from '../api/jobs.js';
import { createJobsStore } from './jobsStore.js';

export function useJobs(status) {
  const { profile, request } = useAuth();
  const store = useMemo(() => createJobsStore({ loadPage: createJobsApi({ request }).listJobs, status }), [request, profile.userId, status]);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => { store.refresh(); return () => store.cancel(); }, [store]);
  return { ...state, refresh: store.refresh, loadMore: store.loadMore };
}
