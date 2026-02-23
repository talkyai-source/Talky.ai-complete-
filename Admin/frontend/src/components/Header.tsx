import { Search, Bell, MessageSquare, ChevronDown } from 'lucide-react';

export function Header() {
    return (
        <header className="header">
            <div className="header-search">
                <Search />
                <input type="text" placeholder="Search..." />
            </div>

            <div className="header-right">
                <div className="header-env">
                    <span className="header-env-dot"></span>
                    <span>Prod</span>
                    <ChevronDown size={14} />
                </div>

                <button className="header-icon-btn">
                    <Bell />
                    <span className="header-badge">5</span>
                </button>

                <button className="header-icon-btn">
                    <MessageSquare />
                </button>

                <div className="header-user">
                    <span>Admin</span>
                </div>
            </div>
        </header>
    );
}
