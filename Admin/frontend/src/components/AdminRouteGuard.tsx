import { Link, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { adminLanding, canAccessAdminRoute, normalizedAdminRole } from '../lib/admin-routes';

interface AdminRouteGuardProps {
    children: React.ReactNode;
    allowedRoles?: string[];
}

// Dev mode flag - set via environment variable
const DEV_MODE = import.meta.env.DEV && import.meta.env.VITE_ADMIN_DEV_MODE === 'true';

/**
 * Admin Route Guard
 * Protects admin routes by checking:
 * 1. User is authenticated
 * 2. User has admin or super_admin role
 *
 * In dev mode (VITE_ADMIN_DEV_MODE=true), bypasses all auth checks.
 */
export function AdminRouteGuard({ children, allowedRoles }: AdminRouteGuardProps) {
    const { isAuthenticated, isLoading, user, logout } = useAuth();
    const location = useLocation();

    // Dev mode bypass - skip all auth checks
    if (DEV_MODE) {
        return <>{children}</>;
    }

    // Show loading state while checking auth
    if (isLoading) {
        return (
            <div className="loading-screen">
                <div className="loading-spinner"></div>
                <p>Verifying access...</p>
            </div>
        );
    }

    // Redirect to login if not authenticated
    if (!isAuthenticated) {
        return <Navigate to="/login" state={{ from: location }} replace />;
    }

    // Double-check admin role (defense in depth). Mirrors the backend's
    // require_admin allow-list: tenant_admin | partner_admin | platform_admin.
    // ('admin'/'super_admin' retained for legacy/dummy-auth.)
    const allowed = user && canAccessAdminRoute(location.pathname, user.role)
        && (!allowedRoles || allowedRoles.some((role) => normalizedAdminRole(role) === normalizedAdminRole(user.role)));
    if (!allowed) {
        return (
            <div className="access-denied">
                <h1>Access Denied</h1>
                <p>You do not have permission to access the admin panel.</p>
                <p>Please contact your system administrator.</p>
                {canAccessAdminRoute(adminLanding(user?.role), user?.role) && <Link to={adminLanding(user?.role)}>Return to your available Admin section</Link>}
                <button type="button" onClick={() => void logout()}>Sign out</button>
            </div>
        );
    }

    return <>{children}</>;
}
