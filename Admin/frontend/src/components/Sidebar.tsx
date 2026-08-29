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
} from 'lucide-react';
import { useAuth } from '../lib/auth';

interface NavItem {
    id: string;
    label: string;
    icon: React.ReactNode;
    path: string;
    platformOnly?: boolean;
}

const navItems: NavItem[] = [
    { id: 'command-center', label: 'Command Center', icon: <LayoutDashboard />, path: '/', platformOnly: true },
    { id: 'tenants', label: 'Tenants', icon: <Building2 />, path: '/tenants', platformOnly: true },
    { id: 'users', label: 'Users & Roles', icon: <Users />, path: '/users', platformOnly: true },
    { id: 'calls', label: 'Calls', icon: <Phone />, path: '/calls', platformOnly: true },
    { id: 'inbound', label: 'Inbound Control', icon: <PhoneIncoming />, path: '/inbound', platformOnly: true },
    { id: 'actions', label: 'Actions', icon: <FileText />, path: '/actions' },
    { id: 'connectors', label: 'Connectors', icon: <Link2 />, path: '/connectors', platformOnly: true },
    { id: 'usage-cost', label: 'Usage & Cost', icon: <DollarSign />, path: '/usage-cost', platformOnly: true },
    { id: 'incidents', label: 'Incidents', icon: <AlertTriangle />, path: '/incidents' },
    { id: 'system-health', label: 'System Health', icon: <Activity />, path: '/system-health' },
];

export function Sidebar() {
    const location = useLocation();
    const { user } = useAuth();
    const canUsePlatformControls = Boolean(user && ['platform_admin', 'super_admin'].includes(user.role));

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
                {navItems.filter((item) => !item.platformOnly || canUsePlatformControls).map((item) => (
                    <Link
                        key={item.id}
                        to={item.path}
                        className={`sidebar-nav-item ${isActive(item.path) ? 'active' : ''}`}
                    >
                        {item.icon}
                        <span>{item.label}</span>
                    </Link>
                ))}
            </nav>
        </aside>
    );
}
