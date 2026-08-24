/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */
import { lazy, Suspense } from 'react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '@/auth/AuthContext';
import RequireAuth from '@/auth/RequireAuth';
import AppLayout from '@/components/AppLayout';
import ErrorBoundary from '@/components/ErrorBoundary';
import AuthPage from '@/pages/Auth';
import HomePage from '@/pages/Home';
import '@arco-design/web-react/dist/css/arco.css';

const InterviewPage = lazy(() => import('@/pages/Interview'));
const ReportPage = lazy(() => import('@/pages/Report'));
const HistoryPage = lazy(() => import('@/pages/History'));
const AnalyticsPage = lazy(() => import('@/pages/Analytics'));

function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <Suspense fallback={<div role="status">页面加载中...</div>}>
            <Routes>
              <Route path="/login" element={<AuthPage mode="login" />} />
              <Route path="/register" element={<AuthPage mode="register" />} />
              <Route element={<RequireAuth />}>
                <Route element={<AppLayout />}>
                  <Route path="/" element={<HomePage />} />
                  <Route path="/history" element={<HistoryPage />} />
                  <Route path="/analytics" element={<AnalyticsPage />} />
                </Route>
                <Route path="/interview/:sessionId" element={<InterviewPage />} />
                <Route path="/report/:reportId" element={<ReportPage />} />
              </Route>
            </Routes>
          </Suspense>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;
