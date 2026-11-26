import { useEffect, useRef } from 'react';
import { useLocation, Link } from 'react-router-dom';
import {
    LayoutDashboard,
    Building2,
    Phone,
    PhoneIncoming,
    Megaphone,
    FileText,
    Link2,
    DollarSign,
    CreditCard,
    AlertTriangle,
    Activity,
    Users,
    Inbox,
    LifeBuoy,
    PhoneForwarded,
    Wallet,
    FileCheck,
} from 'lucide-react';
import { useAuth } from '../lib/auth';
import { ADMIN_ROUTES, canAccessAdminRoute } from '../lib/admin-routes';
import { revealCurrentNavEntry, revealNavEntry } from '../lib/nav-strip';

const icons = { 'command-center': <LayoutDashboard />, tenants: <Building2 />, enquiries: <Inbox />,
    users: <Users />, calls: <Phone />, campaigns: <Megaphone />, trunks: <PhoneForwarded />, payments: <Wallet />, inbound: <PhoneIncoming />, actions: <FileText />,
    connectors: <Link2 />, 'usage-cost': <DollarSign />, plans: <CreditCard />, incidents: <AlertTriangle />, 'system-health': <Activity />,
    support: <LifeBuoy />, 'number-requests': <FileCheck /> };

export function Sidebar() {
    const location = useLocation();
    const { user } = useAuth();
    const navRef = useRef<HTMLElement | null>(null);

    // Small screens show the entries as a sideways strip: bring the current
    // entry into view on load and on each page change.
    useEffect(() => {
        revealCurrentNavEntry(navRef.current);
    }, [location.pathname, user?.role]);

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
                <span className="sidebar-logo-text">Talk-Lee</span>
            </div>

            {/* Tab can land on an entry the strip shows only in part: bring it in whole. */}
            <nav
                className="sidebar-nav"
                ref={navRef}
                onFocus={(event) => revealNavEntry(navRef.current, (event.target as Element).closest('.sidebar-nav-item'))}
            >
                {ADMIN_ROUTES.filter((item) => canAccessAdminRoute(item.path, user?.role)).map((item) => (
                    <Link
                        key={item.id}
                        to={item.path}
                        className={`sidebar-nav-item ${isActive(item.path) ? 'active' : ''}`}
                        aria-current={isActive(item.path) ? 'page' : undefined}
                    >
                        {icons[item.id]}
                        <span>{item.label}</span>
                    </Link>
                ))}
            </nav>
        </aside>
    );
}
