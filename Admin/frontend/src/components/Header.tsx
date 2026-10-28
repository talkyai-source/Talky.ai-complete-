import { LogOut } from 'lucide-react';
import { useAuth } from '../lib/auth';

export function Header() {
    const { user, logout, isLoading } = useAuth();
    return <header className="header">
        <span>Admin workspace</span>
        <div className="header-right">
            <div className="header-user">
                <span>{user?.name || user?.email || 'Verifying identity'}</span>
                {user && <small>{user.email} · {user.role.replace(/_/g, ' ')}</small>}
            </div>
            <button type="button" className="btn btn-secondary btn-sm" disabled={isLoading} onClick={() => void logout()}>
                <LogOut size={16} /> Sign out
            </button>
        </div>
    </header>;
}
