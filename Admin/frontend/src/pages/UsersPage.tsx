import { useState, useEffect, useCallback } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { Users, Search, Filter, ChevronLeft, ChevronRight, RefreshCw, Shield, ShieldCheck, Building2 } from 'lucide-react';
import { api } from '../lib/api';
import type { User } from '../lib/api';

function formatDate(dateStr: string | null): string {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString();
    } catch {
        return dateStr;
    }
}

function RoleBadge({ role }: { role: string }) {
    const isAdmin = role === 'super_admin' || role === 'admin';
    return (
        <span className={`status-badge ${isAdmin ? 'in-progress' : 'queued'}`}>
            {isAdmin ? <ShieldCheck size={12} /> : <Shield size={12} />}
            {role}
        </span>
    );
}

function StatusBadge({ status }: { status: string }) {
    const isActive = status === 'active';
    return (
        <span className={`status-badge ${isActive ? 'in-progress' : 'failed'}`}>
            <span className={`status-dot ${isActive ? 'green' : 'red'}`}></span>
            {status}
        </span>
    );
}

export function UsersPage() {
    const [users, setUsers] = useState<User[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [roleFilter, setRoleFilter] = useState('');
    const [selectedUser, setSelectedUser] = useState<User | null>(null);

    const fetchUsers = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const response = await api.getUsers({
                page: page.toString(),
                limit: pageSize.toString(),
                search: search || undefined,
                role: roleFilter || undefined,
            });
            if (response.data) {
                const usersList = response.data;
                setUsers(usersList);
                setTotal(usersList.length);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch users');
            console.error('Failed to fetch users:', err);
        } finally {
            setLoading(false);
        }
    }, [page, pageSize, search, roleFilter]);

    useEffect(() => {
        fetchUsers();
    }, [fetchUsers]);

    // Debounced search
    useEffect(() => {
        const timer = setTimeout(() => {
            setPage(1);
            fetchUsers();
        }, 300);
        return () => clearTimeout(timer);
    }, [search]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <Users />
                        </div>
                        <div>
                            <h1 className="page-title">Users</h1>
                            <p className="page-description">Manage platform users and their access</p>
                        </div>
                        <div className="page-header-actions">
                            <button
                                className="btn btn-secondary"
                                onClick={fetchUsers}
                                disabled={loading}
                            >
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} />
                                Refresh
                            </button>
                        </div>
                    </div>

                    {error && (
                        <div className="error-banner">
                            <p>{error}</p>
                            <button onClick={fetchUsers}>Retry</button>
                        </div>
                    )}

                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">All Users</h3>
                            <span className="card-count">{total} total</span>
                        </div>
                        <div className="card-body">
                            <div className="table-toolbar">
                                <div className="search-box">
                                    <Search size={16} />
                                    <input
                                        type="text"
                                        placeholder="Search users..."
                                        value={search}
                                        onChange={(e) => setSearch(e.target.value)}
                                        className="search-input"
                                    />
                                </div>
                                <div className="filter-group">
                                    <Filter size={16} />
                                    <select
                                        value={roleFilter}
                                        onChange={(e) => {
                                            setRoleFilter(e.target.value);
                                            setPage(1);
                                        }}
                                        className="filter-select"
                                    >
                                        <option value="">All Roles</option>
                                        <option value="admin">Admin</option>
                                        <option value="super_admin">Super Admin</option>
                                        <option value="user">User</option>
                                    </select>
                                </div>
                            </div>

                            {loading ? (
                                <div className="table-loading">
                                    <div className="loading-spinner"></div>
                                    <p>Loading users...</p>
                                </div>
                            ) : users.length === 0 ? (
                                <div className="empty-state">
                                    <Users size={48} strokeWidth={1} />
                                    <h3>No Users Found</h3>
                                    <p>No users match your current filters.</p>
                                </div>
                            ) : (
                                <>
                                    <table className="data-table clickable-rows">
                                        <thead>
                                            <tr>
                                                <th>Name</th>
                                                <th>Email</th>
                                                <th>Role</th>
                                                <th>Tenant</th>
                                                <th>Status</th>
                                                <th>2FA</th>
                                                <th>Created</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {users.map((user) => (
                                                <tr
                                                    key={user.id}
                                                    onClick={() => setSelectedUser(user)}
                                                    className="clickable-row"
                                                >
                                                    <td>{user.name || '-'}</td>
                                                    <td>{user.email}</td>
                                                    <td><RoleBadge role={user.role} /></td>
                                                    <td>
                                                        <div className="tenant-name-cell">
                                                            <Building2 size={14} />
                                                            <span>{user.tenant_name || user.tenant_id || '-'}</span>
                                                        </div>
                                                    </td>
                                                    <td><StatusBadge status={user.status || 'active'} /></td>
                                                    <td>{user.two_factor_enabled ? 'Enabled' : 'Disabled'}</td>
                                                    <td>{formatDate(user.created_at || null)}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>

                                    <div className="pagination">
                                        <span className="pagination-info">
                                            Showing {((page - 1) * pageSize) + 1} - {Math.min(page * pageSize, total)} of {total}
                                        </span>
                                        <div className="pagination-controls">
                                            <button
                                                className="btn btn-ghost btn-sm"
                                                onClick={() => setPage(p => Math.max(1, p - 1))}
                                                disabled={page === 1}
                                            >
                                                <ChevronLeft size={16} />
                                            </button>
                                            <span className="page-indicator">Page {page} of {totalPages || 1}</span>
                                            <button
                                                className="btn btn-ghost btn-sm"
                                                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                                                disabled={page >= totalPages}
                                            >
                                                <ChevronRight size={16} />
                                            </button>
                                        </div>
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                </div>
            </main>

            {/* User Detail Modal */}
            {selectedUser && (
                <div className="modal-overlay" onClick={() => setSelectedUser(null)}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <div className="modal-header">
                            <h3>User Details</h3>
                            <button className="btn btn-ghost" onClick={() => setSelectedUser(null)}>×</button>
                        </div>
                        <div className="modal-body">
                            <div className="detail-row">
                                <span className="detail-label">Name</span>
                                <span className="detail-value">{selectedUser.name || '-'}</span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">Email</span>
                                <span className="detail-value">{selectedUser.email}</span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">Role</span>
                                <span className="detail-value"><RoleBadge role={selectedUser.role} /></span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">Tenant</span>
                                <span className="detail-value">{selectedUser.tenant_name || selectedUser.tenant_id || '-'}</span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">Status</span>
                                <span className="detail-value"><StatusBadge status={selectedUser.status || 'active'} /></span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">2FA Status</span>
                                <span className="detail-value">{selectedUser.two_factor_enabled ? 'Enabled' : 'Disabled'}</span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">Last Active</span>
                                <span className="detail-value">{formatDate(selectedUser.last_active || null)}</span>
                            </div>
                            <div className="detail-row">
                                <span className="detail-label">Created At</span>
                                <span className="detail-value">{formatDate(selectedUser.created_at || null)}</span>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
