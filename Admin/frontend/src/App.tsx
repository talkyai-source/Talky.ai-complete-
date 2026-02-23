import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './lib/auth';
import { AdminRouteGuard } from './components/AdminRouteGuard';
import { LoginPage } from './pages/LoginPage';
import { CommandCenterPage } from './pages/CommandCenterPage';
import { TenantsPage } from './pages/TenantsPage';
import { CallsPage } from './pages/CallsPage';
import { ActionsPage } from './pages/ActionsPage';
import { ConnectorsPage } from './pages/ConnectorsPage';
import { UsageCostPage } from './pages/UsageCostPage';
import { IncidentsPage } from './pages/IncidentsPage';
import { SystemHealthPage } from './pages/SystemHealthPage';
import './index.css';

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* Public Route */}
          <Route path="/login" element={<LoginPage />} />

          {/* Protected Admin Routes */}
          <Route
            path="/"
            element={
              <AdminRouteGuard>
                <CommandCenterPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/command-center"
            element={
              <AdminRouteGuard>
                <CommandCenterPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/tenants"
            element={
              <AdminRouteGuard>
                <TenantsPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/calls"
            element={
              <AdminRouteGuard>
                <CallsPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/actions"
            element={
              <AdminRouteGuard>
                <ActionsPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/connectors"
            element={
              <AdminRouteGuard>
                <ConnectorsPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/usage-cost"
            element={
              <AdminRouteGuard>
                <UsageCostPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/incidents"
            element={
              <AdminRouteGuard>
                <IncidentsPage />
              </AdminRouteGuard>
            }
          />
          <Route
            path="/system-health"
            element={
              <AdminRouteGuard>
                <SystemHealthPage />
              </AdminRouteGuard>
            }
          />

          {/* Catch all - redirect to dashboard */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
