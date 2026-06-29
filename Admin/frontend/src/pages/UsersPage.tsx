import { useState, useEffect, useCallback } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import {
    Users, UserPlus, Search, Filter, RefreshCw, Shield, ShieldCheck,
    Ban, CheckCircle2, Trash2, Pencil, Building2, X, KeyRound, Lock,
} from 'lucide-react';
import { api } from '../lib/api';
import type {
    AdminUserItem, CreateUserRequest, RbacRole, RbacPermission, TenantListItem,
} from '../lib/api';

// Fallback role list if /rbac/roles is unavailable.
const FALLBACK_ROLES = ['platform_admin', 'partner_admin', 'tenant_admin', 'user', 'readonly'];

const ROLE_LABELS: Record<string, string> = {
    platform_admin: 'Platform Admin',
    partner_admin: 'Partner Admin',
    tenant_admin: 'Tenant Admin',
    user: 'User',
    readonly: 'Read-only',
};

function formatDate(d?: string | null): string {
    if (!d) return '-';
    try { return new Date(d).toLocaleDateString(); } catch { return d; }
}

function isActive(u: AdminUserItem): boolean {
    return u.is_active !== false; // default active when backend omits the field
}

function RoleBadge({ role }: { role: string }) {
    const elevated = role === 'platform_admin' || role === 'partner_admin' || role === 'tenant_admin';
    return (
        <span className={`role-badge ${elevated ? 'role-admin' : 'role-user'}`}>
            {elevated ? <ShieldCheck size={12} /> : <Shield size={12} />}
            {ROLE_LABELS[role] || role}
        </span>
    );
}

export function UsersPage() {
    const [users, setUsers] = useState<AdminUserItem[]>([]);
    const [roles, setRoles] = useState<RbacRole[]>([]);
    const [permissions, setPermissions] = useState<RbacPermission[]>([]);
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [search, setSearch] = useState('');
    const [roleFilter, setRoleFilter] = useState('');
    const [busyId, setBusyId] = useState<string | null>(null);

    const [showAdd, setShowAdd] = useState(false);
    const [editUser, setEditUser] = useState<AdminUserItem | null>(null);
    const [confirmDelete, setConfirmDelete] = useState<AdminUserItem | null>(null);

    const fetchUsers = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.getUsersList({
                search: search || undefined,
                role: roleFilter || undefined,
            });
            if (res.error) {
                setError(res.error.message);
                setUsers([]);
            } else {
                setUsers(res.data ?? []);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch users');
            setUsers([]);
        } finally {
            setLoading(false);
        }
    }, [search, roleFilter]);

    // One-time reference data: roles, permissions, tenants (for the add form).
    useEffect(() => {
        (async () => {
            const [r, p, t] = await Promise.all([
                api.getRoles().catch(() => ({ data: undefined })),
                api.getPermissions().catch(() => ({ data: undefined })),
                api.getTenants().catch(() => ({ data: undefined })),
            ]);
            if (r.data) setRoles(r.data);
            if (p.data) setPermissions(p.data);
            if (t.data) setTenants(t.data);
        })();
    }, []);

    useEffect(() => {
        const t = setTimeout(fetchUsers, 250);
        return () => clearTimeout(t);
    }, [fetchUsers]);

    const flashNotice = (msg: string) => {
        setNotice(msg);
        setTimeout(() => setNotice(null), 3500);
    };

    const roleNames = roles.length ? roles.map((r) => r.name) : FALLBACK_ROLES;

    const toggleBlock = async (u: AdminUserItem) => {
        setBusyId(u.id);
        setError(null);
        const res = await api.updateUser(u.id, { is_active: !isActive(u) });
        setBusyId(null);
        if (res.error) { setError(res.error.message); return; }
        flashNotice(`${u.email} ${isActive(u) ? 'blocked' : 'unblocked'}.`);
        fetchUsers();
    };

    const doDelete = async () => {
        if (!confirmDelete) return;
        const u = confirmDelete;
        setBusyId(u.id);
        setError(null);
        const res = await api.deleteUser(u.id);
        setBusyId(null);
        setConfirmDelete(null);
        if (res.error) { setError(res.error.message); return; }
        flashNotice(`${u.email} deleted.`);
        fetchUsers();
    };

    // Group permissions by resource for the reference panel.
    const permsByResource = permissions.reduce<Record<string, RbacPermission[]>>((acc, p) => {
        (acc[p.resource] ||= []).push(p);
        return acc;
    }, {});

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><Users /></div>
                        <div>
                            <h1 className="page-title">Users &amp; Roles</h1>
                            <p className="page-description">Add users, assign roles, and block or remove access</p>
                        </div>
                        <div className="page-header-actions">
                            <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
                                <UserPlus size={16} /> Add User
                            </button>
                            <button className="btn btn-secondary" onClick={fetchUsers} disabled={loading}>
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} /> Refresh
                            </button>
                        </div>
                    </div>

                    {notice && <div className="success-banner">{notice}</div>}
                    {error && (
                        <div className="error-banner">
                            <p>{error}</p>
                            <button onClick={fetchUsers}>Retry</button>
                        </div>
                    )}

                    {/* Users table */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">All Users</h3>
                            <span className="card-count">{users.length} total</span>
                        </div>
                        <div className="card-body">
                            <div className="table-toolbar">
                                <div className="search-box">
                                    <Search size={16} />
                                    <input
                                        type="text"
                                        placeholder="Search by name or email…"
                                        value={search}
                                        onChange={(e) => setSearch(e.target.value)}
                                        className="search-input"
                                    />
                                </div>
                                <div className="filter-group">
                                    <Filter size={16} />
                                    <select
                                        value={roleFilter}
                                        onChange={(e) => setRoleFilter(e.target.value)}
                                        className="filter-select"
                                    >
                                        <option value="">All Roles</option>
                                        {roleNames.map((r) => (
                                            <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>
                                        ))}
                                    </select>
                                </div>
                            </div>

                            <div className="table-container">
                                {loading ? (
                                    <div className="table-loading">
                                        <RefreshCw className="spinning" size={20} />
                                        <span>Loading users…</span>
                                    </div>
                                ) : users.length === 0 ? (
                                    <div className="empty-state">
                                        <Users size={44} strokeWidth={1} />
                                        <h3>No users found</h3>
                                        <p>Add a user to get started, or adjust your filters.</p>
                                    </div>
                                ) : (
                                    <table className="data-table">
                                        <thead>
                                            <tr>
                                                <th>Name</th>
                                                <th>Email</th>
                                                <th>Role</th>
                                                <th>Tenant</th>
                                                <th>Status</th>
                                                <th>MFA</th>
                                                <th>Created</th>
                                                <th style={{ textAlign: 'right' }}>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {users.map((u) => {
                                                const active = isActive(u);
                                                return (
                                                    <tr key={u.id} className={active ? '' : 'row-blocked'}>
                                                        <td>{u.name || '-'}</td>
                                                        <td>{u.email}</td>
                                                        <td><RoleBadge role={u.role} /></td>
                                                        <td>
                                                            <div className="tenant-name-cell">
                                                                <Building2 size={14} />
                                                                <span>{u.tenant_name || u.tenant_id || '—'}</span>
                                                            </div>
                                                        </td>
                                                        <td>
                                                            <span className={`status-badge ${active ? 'status-active' : 'status-error'}`}>
                                                                <span className={`status-dot ${active ? 'green' : 'red'}`} />
                                                                {active ? 'Active' : 'Blocked'}
                                                            </span>
                                                        </td>
                                                        <td>{u.mfa_enabled ? 'On' : 'Off'}</td>
                                                        <td>{formatDate(u.created_at)}</td>
                                                        <td>
                                                            <div className="user-actions">
                                                                <button
                                                                    className="icon-btn"
                                                                    title="Edit role / name"
                                                                    onClick={() => setEditUser(u)}
                                                                >
                                                                    <Pencil size={15} />
                                                                </button>
                                                                <button
                                                                    className={`icon-btn ${active ? 'warn' : 'ok'}`}
                                                                    title={active ? 'Block user' : 'Unblock user'}
                                                                    disabled={busyId === u.id}
                                                                    onClick={() => toggleBlock(u)}
                                                                >
                                                                    {active ? <Ban size={15} /> : <CheckCircle2 size={15} />}
                                                                </button>
                                                                <button
                                                                    className="icon-btn danger"
                                                                    title="Delete user"
                                                                    disabled={busyId === u.id}
                                                                    onClick={() => setConfirmDelete(u)}
                                                                >
                                                                    <Trash2 size={15} />
                                                                </button>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Roles & Permissions reference */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title"><KeyRound size={18} /> Roles &amp; Permissions</h3>
                            <span className="card-count">{roleNames.length} roles</span>
                        </div>
                        <div className="card-body roles-panel">
                            <div className="roles-list">
                                {(roles.length ? roles : FALLBACK_ROLES.map((name, i) => ({
                                    id: name, name, description: null,
                                    level: (5 - i) * 20, is_system_role: true, tenant_scoped: false,
                                })) as RbacRole[])
                                    .sort((a, b) => b.level - a.level)
                                    .map((r) => (
                                        <div key={r.id} className="role-row">
                                            <div className="role-row-main">
                                                <RoleBadge role={r.name} />
                                                <span className="role-level">level {r.level}</span>
                                            </div>
                                            <p className="role-desc">
                                                {r.description || 'No description provided.'}
                                            </p>
                                        </div>
                                    ))}
                            </div>

                            {permissions.length > 0 && (
                                <div className="perms-block">
                                    <h4 className="perms-title"><Lock size={14} /> Permissions ({permissions.length})</h4>
                                    <div className="perms-grid">
                                        {Object.entries(permsByResource).sort().map(([res, perms]) => (
                                            <div key={res} className="perm-group">
                                                <span className="perm-resource">{res}</span>
                                                <div className="perm-actions">
                                                    {perms.map((p) => (
                                                        <span key={p.id} className="perm-chip" title={p.description || p.name}>
                                                            {p.action}
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </main>

            {showAdd && (
                <AddUserModal
                    roleNames={roleNames}
                    tenants={tenants}
                    onClose={() => setShowAdd(false)}
                    onCreated={(email) => { setShowAdd(false); flashNotice(`${email} created.`); fetchUsers(); }}
                />
            )}

            {editUser && (
                <EditUserModal
                    user={editUser}
                    roleNames={roleNames}
                    onClose={() => setEditUser(null)}
                    onSaved={(email) => { setEditUser(null); flashNotice(`${email} updated.`); fetchUsers(); }}
                />
            )}

            {confirmDelete && (
                <div className="modal-overlay" onClick={() => setConfirmDelete(null)}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <h3 className="modal-title">Delete user</h3>
                        <p className="modal-description">
                            Permanently delete <strong>{confirmDelete.email}</strong>? This cannot be undone.
                            If the user has activity, consider <strong>blocking</strong> instead.
                        </p>
                        <div className="modal-actions">
                            <button className="btn btn-secondary" onClick={() => setConfirmDelete(null)}>Cancel</button>
                            <button className="btn btn-danger" onClick={doDelete} disabled={busyId === confirmDelete.id}>
                                <Trash2 size={15} /> Delete
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ── Add User modal ──────────────────────────────────────────────────────────
function AddUserModal({ roleNames, tenants, onClose, onCreated }: {
    roleNames: string[];
    tenants: TenantListItem[];
    onClose: () => void;
    onCreated: (email: string) => void;
}) {
    const [form, setForm] = useState<CreateUserRequest>({
        name: '', email: '', password: '', role: 'user', tenant_id: '',
    });
    const [submitting, setSubmitting] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const set = (k: keyof CreateUserRequest, v: string) => setForm((f) => ({ ...f, [k]: v }));

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!form.name || !form.email || !form.password) {
            setErr('Name, email and a temporary password are required.');
            return;
        }
        setSubmitting(true);
        setErr(null);
        const res = await api.createUser({
            ...form,
            tenant_id: form.tenant_id || null,
        });
        setSubmitting(false);
        if (res.error) { setErr(res.error.message); return; }
        onCreated(form.email);
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <form className="modal-content user-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
                <div className="modal-title-row">
                    <h3 className="modal-title">Add user</h3>
                    <button type="button" className="icon-btn" onClick={onClose}><X size={18} /></button>
                </div>

                {err && <div className="error-banner inline"><p>{err}</p></div>}

                <div className="form-group">
                    <label>Full name</label>
                    <input className="form-input" value={form.name}
                        onChange={(e) => set('name', e.target.value)} placeholder="Jane Doe" autoFocus />
                </div>
                <div className="form-group">
                    <label>Email</label>
                    <input className="form-input" type="email" value={form.email}
                        onChange={(e) => set('email', e.target.value)} placeholder="jane@company.com" />
                </div>
                <div className="form-group">
                    <label>Temporary password</label>
                    <input className="form-input" type="text" value={form.password}
                        onChange={(e) => set('password', e.target.value)} placeholder="They can change it after first login" />
                    <span className="form-help">Share this with the user securely; they sign in with it.</span>
                </div>
                <div className="form-row">
                    <div className="form-group">
                        <label>Role</label>
                        <select className="form-input" value={form.role} onChange={(e) => set('role', e.target.value)}>
                            {roleNames.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
                        </select>
                    </div>
                    <div className="form-group">
                        <label>Tenant (optional)</label>
                        <select className="form-input" value={form.tenant_id || ''} onChange={(e) => set('tenant_id', e.target.value)}>
                            <option value="">— None —</option>
                            {tenants.map((t) => <option key={t.id} value={t.id}>{t.business_name}</option>)}
                        </select>
                    </div>
                </div>

                <div className="modal-actions">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={submitting}>
                        <UserPlus size={15} /> {submitting ? 'Creating…' : 'Create user'}
                    </button>
                </div>
            </form>
        </div>
    );
}

// ── Edit User modal (name + role) ───────────────────────────────────────────
function EditUserModal({ user, roleNames, onClose, onSaved }: {
    user: AdminUserItem;
    roleNames: string[];
    onClose: () => void;
    onSaved: (email: string) => void;
}) {
    const [name, setName] = useState(user.name ?? '');
    const [role, setRole] = useState(user.role);
    const [submitting, setSubmitting] = useState(false);
    const [err, setErr] = useState<string | null>(null);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        setErr(null);
        const res = await api.updateUser(user.id, { name, role });
        setSubmitting(false);
        if (res.error) { setErr(res.error.message); return; }
        onSaved(user.email);
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <form className="modal-content user-modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
                <div className="modal-title-row">
                    <h3 className="modal-title">Edit user</h3>
                    <button type="button" className="icon-btn" onClick={onClose}><X size={18} /></button>
                </div>
                <p className="modal-description">{user.email}</p>

                {err && <div className="error-banner inline"><p>{err}</p></div>}

                <div className="form-group">
                    <label>Full name</label>
                    <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="form-group">
                    <label>Role</label>
                    <select className="form-input" value={role} onChange={(e) => setRole(e.target.value)}>
                        {roleNames.map((r) => <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>)}
                    </select>
                </div>

                <div className="modal-actions">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={submitting}>
                        {submitting ? 'Saving…' : 'Save changes'}
                    </button>
                </div>
            </form>
        </div>
    );
}
