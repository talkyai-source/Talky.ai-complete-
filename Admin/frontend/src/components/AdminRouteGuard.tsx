import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth';

interface AdminRouteGuardProps {
    children: React.ReactNode;
}

// Dev mode flag - set via environment variable
const DEV_MODE = import.meta.env.VITE_ADMIN_DEV_MODE === 'true';

/**
 * Admin Route Guard
 * Protects admin routes by checking:
 * 1. User is authenticated
 * 2. User has admin or super_admin role
 * 
 * In dev mode (VITE_ADMIN_DEV_MODE=true), bypasses all auth checks.
 */
export function AdminRouteGuard({ children }: AdminRouteGuardProps) {
    const { isAuthenticated, isLoading, user } = useAuth();
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

    // Double-check admin role (defense in depth)
    if (user?.role !== 'admin' && user?.role !== 'super_admin') {
        return (
            <div className="access-denied">
                <h1>Access Denied</h1>
                <p>You do not have permission to access the admin panel.</p>
                <p>Please contact your system administrator.</p>
            </div>
        );
    }

    return <>{children}</>;
}
