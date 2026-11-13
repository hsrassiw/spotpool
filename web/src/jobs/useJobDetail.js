import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { useAuth } from '../auth/AuthContext.jsx';
import { createJobDetailApi } from '../api/jobDetail.js';
import { createJobSubmissionApi } from '../api/jobSubmission.js';
import { readLog } from '../api/readLog.js';
import { createJobDetailStore } from './jobDetailStore.js';
import { createTaskFilesStore } from './taskFilesStore.js';
import { createCancelJobStore } from './cancelJobStore.js';

export function useJobDetail(jobId) {
  const { profile, request, getSessionSignal, refreshProfile } = useAuth();
  const sessionSignal = getSessionSignal();
  const store = useMemo(() => createJobDetailStore({ loadJob: (options) => createJobDetailApi({ request }).getJob(jobId, options) }),
    [jobId, profile.userId, request, sessionSignal]);
  const files = useMemo(() => createTaskFilesStore({ getCurrentJob: () => store.getSnapshot().job, refreshJob: store.load,
    readLog, openWindow: () => window.open('about:blank', '_blank') }), [store]);
  const cancelAction = useMemo(() => createCancelJobStore({ getJob: () => store.getSnapshot().job, refreshJob: store.refresh,
    cancelJob: (options) => createJobSubmissionApi({ request }).cancelJob(jobId, options), refreshProfile }), [store, request, jobId, refreshProfile]);
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const fileState = useSyncExternalStore(files.subscribe, files.getSnapshot, files.getSnapshot);
  const cancelState = useSyncExternalStore(cancelAction.subscribe, cancelAction.getSnapshot, cancelAction.getSnapshot);

  useEffect(() => {
    const visibility = () => store.setVisible(document.visibilityState !== 'hidden');
    const stop = () => { store.stop(); files.stop(); cancelAction.stop(); };
    if (!sessionSignal.aborted) { files.start(); cancelAction.start(); store.start(document.visibilityState !== 'hidden'); }
    document.addEventListener('visibilitychange', visibility);
    sessionSignal.addEventListener('abort', stop, { once: true });
    return () => {
      document.removeEventListener('visibilitychange', visibility);
      sessionSignal.removeEventListener('abort', stop);
      stop();
    };
  }, [store, files, cancelAction, sessionSignal]);

  useEffect(() => { if (!state.job && state.error) files.closeLog(); }, [state.job, state.error, files]);
  return { state, fileState, cancelState, cancelAction, refresh: store.refresh, files };
}
