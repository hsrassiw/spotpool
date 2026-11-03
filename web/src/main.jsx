import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router';
import App from './app/App.jsx';
import { AuthProvider } from './auth/AuthContext.jsx';
import './styles/tokens.css';
import './styles/global.css';

// Data router cho phép chặn cả link lẫn Back/Forward khi đang tạo/upload job.
const router = createBrowserRouter([{ path: '*', element: <AuthProvider><App /></AuthProvider> }]);
createRoot(document.getElementById('root')).render(<StrictMode><RouterProvider router={router} /></StrictMode>);
