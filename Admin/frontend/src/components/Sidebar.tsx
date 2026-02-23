import { useLocation, Link } from 'react-router-dom';
import {
    LayoutDashboard,
    Building2,
    Phone,
    FileText,
    Link2,
    DollarSign,
    AlertTriangle,
    Activity,
    Zap
} from 'lucide-react';

interface NavItem {
    id: string;
    label: string;
    icon: React.ReactNode;
    path: string;
}

const navItems: NavItem[] = [
    { id: 'command-center', label: 'Command Center', icon: <LayoutDashboard />, path: '/' },
    { id: 'tenants', label: 'Tenants', icon: <Building2 />, path: '/tenants' },
    { id: 'calls', label: 'Calls', icon: <Phone />, path: '/calls' },
    { id: 'actions', label: 'Actions', icon: <FileText />, path: '/actions' },
    { id: 'connectors', label: 'Connectors', icon: <Link2 />, path: '/connectors' },
    { id: 'usage-cost', label: 'Usage & Cost', icon: <DollarSign />, path: '/usage-cost' },
    { id: 'incidents', label: 'Incidents', icon: <AlertTriangle />, path: '/incidents' },
    { id: 'system-health', label: 'System Health', icon: <Activity />, path: '/system-health' },
];

export function Sidebar() {
    const location = useLocation();

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
                    <Zap size={18} />
                </div>
                <span className="sidebar-logo-text">Talk-lee</span>
            </div>

            <nav className="sidebar-nav">
                {navItems.map((item) => (
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
