import { useState } from 'react';
import {
    Building2,
    Search,
    Filter,
    MoreVertical,
    Pause,
    Play,
    Settings,
    Users,
    Megaphone,
    ChevronUp,
    ChevronDown
} from 'lucide-react';
import type { TenantListItem, QuotaUpdateRequest } from '../lib/api';
import { api } from '../lib/api';

interface TenantsTableProps {
    tenants: TenantListItem[];
    loading: boolean;
    onRefresh: () => void;
    searchTerm: string;
    onSearchChange: (term: string) => void;
    statusFilter: string;
    onStatusFilterChange: (status: string) => void;
}

type SortField = 'business_name' | 'status' | 'minutes_used' | 'user_count';
type SortDirection = 'asc' | 'desc';

export function TenantsTable({
    tenants,
    loading,
    onRefresh,
    searchTerm,
    onSearchChange,
    statusFilter,
    onStatusFilterChange
}: TenantsTableProps) {
    const [sortField, setSortField] = useState<SortField>('business_name');
    const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
    const [activeMenu, setActiveMenu] = useState<string | null>(null);
    const [quotaModal, setQuotaModal] = useState<{ open: boolean; tenant: TenantListItem | null }>({
        open: false,
        tenant: null
    });
    const [confirmAction, setConfirmAction] = useState<{
        open: boolean;
        tenant: TenantListItem | null;
        action: 'suspend' | 'resume';
    }>({ open: false, tenant: null, action: 'suspend' });
    const [actionLoading, setActionLoading] = useState(false);

    // Sort tenants
    const sortedTenants = [...tenants].sort((a, b) => {
        let aVal: string | number;
        let bVal: string | number;

        switch (sortField) {
            case 'business_name':
                aVal = a.business_name.toLowerCase();
                bVal = b.business_name.toLowerCase();
                break;
            case 'status':
                aVal = a.status;
                bVal = b.status;
                break;
            case 'minutes_used':
                aVal = a.minutes_used;
                bVal = b.minutes_used;
                break;
            case 'user_count':
                aVal = a.user_count;
                bVal = b.user_count;
                break;
            default:
                return 0;
        }

        if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
        return 0;
    });

    const handleSort = (field: SortField) => {
        if (sortField === field) {
            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDirection('asc');
        }
    };

    const renderSortIcon = (field: SortField) => {
        if (sortField !== field) return null;
        return sortDirection === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />;
    };

    const getStatusClass = (status: string) => {
        switch (status) {
            case 'active': return 'status-badge status-active';
            case 'suspended': return 'status-badge status-suspended';
            case 'inactive': return 'status-badge status-inactive';
            default: return 'status-badge';
        }
    };

    const getUsagePercent = (used: number, allocated: number) => {
        if (allocated === 0) return 0;
        return Math.min((used / allocated) * 100, 100);
    };

    const handleSuspendResume = async () => {
        if (!confirmAction.tenant) return;
        setActionLoading(true);
        try {
            if (confirmAction.action === 'suspend') {
                await api.suspendTenant(confirmAction.tenant.id);
            } else {
                await api.resumeTenant(confirmAction.tenant.id);
            }
            onRefresh();
        } catch (error) {
            console.error('Failed to update tenant status:', error);
        } finally {
            setActionLoading(false);
            setConfirmAction({ open: false, tenant: null, action: 'suspend' });
        }
    };

    const handleQuotaUpdate = async (quota: QuotaUpdateRequest) => {
        if (!quotaModal.tenant) return;
        setActionLoading(true);
        try {
            await api.updateTenantQuota(quotaModal.tenant.id, quota);
            onRefresh();
            setQuotaModal({ open: false, tenant: null });
        } catch (error) {
            console.error('Failed to update quota:', error);
        } finally {
            setActionLoading(false);
        }
    };

    return (
        <>
            {/* Search and Filter Bar */}
            <div className="table-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        type="text"
                        placeholder="Search tenants..."
                        value={searchTerm}
                        onChange={(e) => onSearchChange(e.target.value)}
                        className="search-input"
                    />
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        value={statusFilter}
                        onChange={(e) => onStatusFilterChange(e.target.value)}
                        className="filter-select"
                    >
                        <option value="">All Statuses</option>
                        <option value="active">Active</option>
                        <option value="suspended">Suspended</option>
                        <option value="inactive">Inactive</option>
                    </select>
                </div>
            </div>

            {/* Tenants Table */}
            <div className="table-container">
                {loading ? (
                    <div className="table-loading">
                        <div className="loading-spinner"></div>
                        <p>Loading tenants...</p>
                    </div>
                ) : sortedTenants.length === 0 ? (
                    <div className="empty-state">
                        <Building2 size={48} />
                        <h3>No tenants found</h3>
                        <p>Try adjusting your search or filter criteria.</p>
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th onClick={() => handleSort('business_name')} className="sortable">
                                    Tenant Name {renderSortIcon('business_name')}
                                </th>
                                <th>Plan</th>
                                <th onClick={() => handleSort('status')} className="sortable">
                                    Status {renderSortIcon('status')}
                                </th>
                                <th onClick={() => handleSort('minutes_used')} className="sortable">
                                    Usage {renderSortIcon('minutes_used')}
                                </th>
                                <th onClick={() => handleSort('user_count')} className="sortable">
                                    Users {renderSortIcon('user_count')}
                                </th>
                                <th>Campaigns</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedTenants.map((tenant) => (
                                <tr key={tenant.id}>
                                    <td>
                                        <div className="tenant-name-cell">
                                            <Building2 size={16} />
                                            <span>{tenant.business_name}</span>
                                        </div>
                                    </td>
                                    <td>
                                        <span className="plan-badge">
                                            {tenant.plan_name || tenant.plan_id || 'No Plan'}
                                        </span>
                                    </td>
                                    <td>
                                        <span className={getStatusClass(tenant.status)}>
                                            {tenant.status.charAt(0).toUpperCase() + tenant.status.slice(1)}
                                        </span>
                                    </td>
                                    <td>
                                        <div className="usage-cell">
                                            <div className="usage-bar">
                                                <div
                                                    className="usage-fill"
                                                    style={{
                                                        width: `${getUsagePercent(tenant.minutes_used, tenant.minutes_allocated)}%`,
                                                        backgroundColor: getUsagePercent(tenant.minutes_used, tenant.minutes_allocated) > 90
                                                            ? 'var(--color-error)'
                                                            : 'var(--color-primary)'
                                                    }}
                                                />
                                            </div>
                                            <span className="usage-text">
                                                {tenant.minutes_used.toLocaleString()} / {tenant.minutes_allocated.toLocaleString()} min
                                            </span>
                                        </div>
                                    </td>
                                    <td>
                                        <div className="count-cell">
                                            <Users size={14} />
                                            <span>{tenant.user_count}</span>
                                        </div>
                                    </td>
                                    <td>
                                        <div className="count-cell">
                                            <Megaphone size={14} />
                                            <span>{tenant.campaign_count}</span>
                                        </div>
                                    </td>
                                    <td>
                                        <div className="actions-cell">
                                            <button
                                                className="action-menu-btn"
                                                onClick={() => setActiveMenu(activeMenu === tenant.id ? null : tenant.id)}
                                            >
                                                <MoreVertical size={16} />
                                            </button>
                                            {activeMenu === tenant.id && (
                                                <div className="action-menu">
                                                    {tenant.status === 'active' ? (
                                                        <button
                                                            className="action-menu-item danger"
                                                            onClick={() => {
                                                                setConfirmAction({
                                                                    open: true,
                                                                    tenant,
                                                                    action: 'suspend'
                                                                });
                                                                setActiveMenu(null);
                                                            }}
                                                        >
                                                            <Pause size={14} />
                                                            Suspend Tenant
                                                        </button>
                                                    ) : (
                                                        <button
                                                            className="action-menu-item"
                                                            onClick={() => {
                                                                setConfirmAction({
                                                                    open: true,
                                                                    tenant,
                                                                    action: 'resume'
                                                                });
                                                                setActiveMenu(null);
                                                            }}
                                                        >
                                                            <Play size={14} />
                                                            Resume Tenant
                                                        </button>
                                                    )}
                                                    <button
                                                        className="action-menu-item"
                                                        onClick={() => {
                                                            setQuotaModal({ open: true, tenant });
                                                            setActiveMenu(null);
                                                        }}
                                                    >
                                                        <Settings size={14} />
                                                        Edit Quota
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Confirm Modal */}
            {confirmAction.open && confirmAction.tenant && (
                <div className="modal-overlay" onClick={() => setConfirmAction({ open: false, tenant: null, action: 'suspend' })}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <h3 className="modal-title">
                            {confirmAction.action === 'suspend' ? 'Suspend Tenant?' : 'Resume Tenant?'}
                        </h3>
                        <p className="modal-description">
                            {confirmAction.action === 'suspend'
                                ? `Are you sure you want to suspend "${confirmAction.tenant.business_name}"? This will block all calls and platform access.`
                                : `Are you sure you want to resume "${confirmAction.tenant.business_name}"? This will restore full platform access.`
                            }
                        </p>
                        <div className="modal-actions">
                            <button
                                className="btn btn-secondary"
                                onClick={() => setConfirmAction({ open: false, tenant: null, action: 'suspend' })}
                                disabled={actionLoading}
                            >
                                Cancel
                            </button>
                            <button
                                className={`btn ${confirmAction.action === 'suspend' ? 'btn-danger' : 'btn-primary'}`}
                                onClick={handleSuspendResume}
                                disabled={actionLoading}
                            >
                                {actionLoading ? 'Processing...' : confirmAction.action === 'suspend' ? 'Suspend' : 'Resume'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Quota Modal */}
            {quotaModal.open && quotaModal.tenant && (
                <QuotaModal
                    tenant={quotaModal.tenant}
                    onClose={() => setQuotaModal({ open: false, tenant: null })}
                    onSave={handleQuotaUpdate}
                    loading={actionLoading}
                />
            )}
        </>
    );
}

// Quota Modal Component
interface QuotaModalProps {
    tenant: TenantListItem;
    onClose: () => void;
    onSave: (quota: QuotaUpdateRequest) => void;
    loading: boolean;
}

function QuotaModal({ tenant, onClose, onSave, loading }: QuotaModalProps) {
    const [minutes, setMinutes] = useState(tenant.minutes_allocated);
    const [concurrentCalls, setConcurrentCalls] = useState(tenant.max_concurrent_calls);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        onSave({
            minutes_allocated: minutes,
            max_concurrent_calls: concurrentCalls
        });
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-content quota-modal" onClick={(e) => e.stopPropagation()}>
                <h3 className="modal-title">Edit Quota - {tenant.business_name}</h3>
                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label htmlFor="minutes">Minutes Allocated</label>
                        <input
                            type="number"
                            id="minutes"
                            value={minutes}
                            onChange={(e) => setMinutes(parseInt(e.target.value) || 0)}
                            min={0}
                            className="form-input"
                        />
                        <span className="form-help">Current usage: {tenant.minutes_used} minutes</span>
                    </div>
                    <div className="form-group">
                        <label htmlFor="concurrent">Max Concurrent Calls</label>
                        <input
                            type="number"
                            id="concurrent"
                            value={concurrentCalls}
                            onChange={(e) => setConcurrentCalls(parseInt(e.target.value) || 1)}
                            min={1}
                            max={100}
                            className="form-input"
                        />
                    </div>
                    <div className="modal-actions">
                        <button
                            type="button"
                            className="btn btn-secondary"
                            onClick={onClose}
                            disabled={loading}
                        >
                            Cancel
                        </button>
                        <button
                            type="submit"
                            className="btn btn-primary"
                            disabled={loading}
                        >
                            {loading ? 'Saving...' : 'Save Changes'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
