import { Navigate, Route, Routes } from 'react-router';
import AppLayout from './AppLayout.jsx';
import JobsPage from '../pages/JobsPage.jsx';
import JobDetailPage from '../pages/JobDetailPage.jsx';
import AccountPage from '../pages/AccountPage.jsx';
import AccessPage from '../pages/AccessPage.jsx';
import { AdminPage, BillingPage, CreateJobPage, NotFoundPage } from '../pages/PlannedPages.jsx';
import { RequireAdmin, RequireAuth } from '../auth/RequireAuth.jsx';

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<AccessPage key="login" />} />
      <Route path="/register" element={<AccessPage key="register" register />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Navigate to="/jobs" replace />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/new" element={<CreateJobPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/billing" element={<BillingPage />} />
          <Route element={<RequireAdmin />}>
            <Route path="/admin" element={<AdminPage />} />
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
