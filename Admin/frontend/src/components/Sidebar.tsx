import { useLocation, Link } from 'react-router-dom';
import {
    LayoutDashboard,
    Building2,
    Phone,
    PhoneIncoming,
    FileText,
    Link2,
    DollarSign,
    AlertTriangle,
    Activity,
    Users,
    Inbox,
} from 'lucide-react';
import { useAuth } from '../lib/auth';
import { ADMIN_ROUTES, canAccessAdminRoute } from '../lib/admin-routes';

const icons = { 'command-center': <LayoutDashboard />, tenants: <Building2 />, enquiries: <Inbox />,
    users: <Users />, calls: <Phone />, inbound: <PhoneIncoming />, actions: <FileText />,
    connectors: <Link2 />, 'usage-cost': <DollarSign />, incidents: <AlertTriangle />, 'system-health': <Activity /> };

export function Sidebar() {
    const location = useLocation();
    const { user } = useAuth();

    const isActive = (path: string) => {
        if (path === '/') {
            return location.pathname === '/' || location.pathname === '/command-center';
        }
        return location.pathname === path;
    };

    return (
        <aside className="sidebar">
            <div className="sidebar-logo">
                <div className="sidebar-logo-icon">
                    <img src="/logo.svg" alt="Talk-Lee" style={{ width: 20, height: 20 }} />
                </div>
                <span className="sidebar-logo-text">Talk-lee</span>
            </div>

            <nav className="sidebar-nav">
                {ADMIN_ROUTES.filter((item) => canAccessAdminRoute(item.path, user?.role)).map((item) => (
                    <Link
                        key={item.id}
                        to={item.path}
                        className={`sidebar-nav-item ${isActive(item.path) ? 'active' : ''}`}
                    >
                        {icons[item.id]}
                        <span>{item.label}</span>
                    </Link>
                ))}
            </nav>
        </aside>
    );
}
