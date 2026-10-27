import { useCallback, useEffect, useId, useRef, useState, type RefObject } from 'react';
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
import type { TenantListItem, QuotaUpdateRequest, TenantMutationReceipt } from '../lib/api';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useDialogFocus } from '../lib/use-dialog-focus';
import { ConfirmationModal } from './ConfirmationModal';

interface TenantsTableProps {
    tenants: TenantListItem[];
    loading: boolean;
    onRefresh: () => void | Promise<void>;
    searchTerm: string;
    onSearchChange: (term: string) => void;
    statusFilter: string;
    onStatusFilterChange: (status: string) => void;
    readError?: string | null;
    onFormalHolds?: (tenant: TenantListItem) => void;
    searchInputRef?: RefObject<HTMLInputElement | null>;
}

type SortField = 'business_name' | 'status' | 'minutes_used' | 'user_count';
type SortDirection = 'asc' | 'desc';

function savedTenantReceipt(receipt: TenantMutationReceipt | undefined, id: string): receipt is TenantMutationReceipt {
    return Boolean(receipt && receipt.id === id && typeof receipt.changed === 'boolean'
        && (receipt.updated_at === null || (typeof receipt.updated_at === 'string' && Number.isFinite(Date.parse(receipt.updated_at))))
        && (!receipt.changed || (receipt.updated_at !== null && typeof receipt.audit_event_id === 'string' && receipt.audit_event_id.length > 0)));
}

export function TenantsTable(props: TenantsTableProps) {
    const generation = useAuthGeneration();
    return <OwnedTenantsTable key={generation} {...props} generation={generation} />;
}

function OwnedTenantsTable({
    tenants,
    loading,
    onRefresh,
    searchTerm,
    onSearchChange,
    statusFilter,
    onStatusFilterChange, readError, generation, onFormalHolds, searchInputRef
}: TenantsTableProps & { generation: number }) {
    const subscriptionHelpId = useId();
    const mounted = useRef(false), pending = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const owns = useCallback(() => mounted.current && generation === api.getAuthGeneration(), [generation]);
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
    // The API client never throws: a 403/500/network failure comes back as
    // { error }. Keep the modal open and show it rather than looking like success.
    const [actionError, setActionError] = useState<string | null>(null);
    const [savedChange, setSavedChange] = useState<{
        tenantId: string; tenantName: string; summary: string;
        version: string | null; auditId: string | null;
    } | null>(null);
    const [savedRefreshError, setSavedRefreshError] = useState(false);
    const savedSequence = useRef(0);

    const acknowledgeSave = (tenant: TenantListItem, receipt: TenantMutationReceipt, summary: string) => {
        const sequence = ++savedSequence.current;
        setSavedChange({ tenantId: tenant.id, tenantName: tenant.business_name, summary,
            version: receipt.updated_at, auditId: typeof receipt.audit_event_id === 'string' ? receipt.audit_event_id : null });
        setSavedRefreshError(false);
        // A failed follow-up read cannot undo the already confirmed write.
        void (async () => {
            try { await onRefresh(); }
            catch { if (owns() && savedSequence.current === sequence) setSavedRefreshError(true); }
        })();
    };

    // Only the current server page is present in this component.
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

    const closeConfirm = () => {
        if (pending.current) return;
        setActionError(null);
        setConfirmAction({ open: false, tenant: null, action: 'suspend' });
    };

    const closeQuota = () => {
        if (pending.current) return;
        setActionError(null);
        setQuotaModal({ open: false, tenant: null });
    };

    const handleSuspendResume = async () => {
        if (!confirmAction.tenant || !owns() || pending.current) return;
        if (confirmAction.tenant.updated_at === undefined) { setActionError('The tenant version is missing. Close this dialog and refresh the list.'); return; }
        pending.current = true;
        setActionLoading(true);
        setActionError(null);
        try {
            const res = confirmAction.action === 'suspend'
                ? await api.suspendTenant(confirmAction.tenant.id, confirmAction.tenant.updated_at)
                : await api.resumeTenant(confirmAction.tenant.id, confirmAction.tenant.updated_at);
            if (!owns()) return;
            if (res.error || !savedTenantReceipt(res.data, confirmAction.tenant.id) || res.data.status !== (confirmAction.action === 'suspend' ? 'suspended' : 'active')) {
                setActionError(res.error?.message || 'The saved tenant status could not be confirmed. Close this dialog and refresh the list.');
                return;
            }
            acknowledgeSave(confirmAction.tenant, res.data, `Subscription ${res.data.status}.`);
            pending.current = false;
            closeConfirm();
        } catch (error) {
            if (owns()) setActionError(error instanceof Error ? error.message : 'The tenant status could not be confirmed.');
        } finally {
            if (owns()) { pending.current = false; setActionLoading(false); }
        }
    };

    const handleQuotaUpdate = async (quota: QuotaUpdateRequest) => {
        if (!quotaModal.tenant || !owns() || pending.current) return;
        if (quotaModal.tenant.updated_at === undefined) { setActionError('The tenant version is missing. Close this dialog and refresh the list.'); return; }
        pending.current = true;
        setActionLoading(true);
        setActionError(null);
        try {
            const res = await api.updateTenantQuota(quotaModal.tenant.id, quota);
            if (!owns()) return;
            if (res.error || !savedTenantReceipt(res.data, quotaModal.tenant.id) || (quota.minutes_allocated !== undefined && res.data.minutes_allocated !== quota.minutes_allocated)
                || (quota.max_concurrent_calls !== undefined && res.data.max_concurrent_calls !== quota.max_concurrent_calls)) {
                setActionError(res.error?.message || 'The saved quota could not be confirmed. Close this dialog and refresh the list.');
                return;
            }
            const fields: string[] = [];
            if (quota.minutes_allocated !== undefined) fields.push(`Minutes allocated: ${res.data.minutes_allocated}`);
            if (quota.max_concurrent_calls !== undefined) fields.push(`Maximum concurrent calls: ${res.data.max_concurrent_calls}`);
            acknowledgeSave(quotaModal.tenant, res.data, `${fields.join('; ')}.`);
            pending.current = false;
            closeQuota();
        } catch (error) {
            if (owns()) setActionError(error instanceof Error ? error.message : 'The saved quota could not be confirmed.');
        } finally {
            if (owns()) { pending.current = false; setActionLoading(false); }
        }
    };

    return (
        <>
            {savedChange && <div className="success-banner" role="status" aria-label="Saved tenant change">
                <div>
                    <p>Saved change confirmed for {savedChange.tenantName} ({savedChange.tenantId}). {savedChange.summary}</p>
                    <p>This is a saved result, not a current-state check.</p>
                    {savedChange.version && <p>Saved version: {savedChange.version}</p>}
                    <p>{savedChange.auditId ? `Audit reference: ${savedChange.auditId}` : 'The requested state already existed; no new mutation audit was created.'}</p>
                    {savedRefreshError && <p role="alert">Saved change confirmed, but the directory reload failed. Use Refresh to check current state.</p>}
                </div>
                <button type="button" onClick={() => { savedSequence.current += 1; setSavedChange(null); setSavedRefreshError(false); }}>Dismiss saved tenant change</button>
            </div>}
            {/* Search and Filter Bar */}
            <div className="table-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        type="text"
                        placeholder="Search tenants..."
                        aria-label="Search tenants"
                        ref={searchInputRef}
                        value={searchTerm}
                        onChange={(e) => onSearchChange(e.target.value)}
                        className="search-input"
                    />
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        aria-label="Tenant subscription status"
                        aria-describedby={subscriptionHelpId}
                        value={statusFilter}
                        onChange={(e) => onStatusFilterChange(e.target.value)}
                        className="filter-select"
                    >
                        <option value="">All subscription statuses</option>
                        <option value="active">Active</option>
                        <option value="suspended">Suspended</option>
                        <option value="inactive">Inactive</option>
                        <option value="trialing">Trialing</option>
                        <option value="past_due">Past due</option>
                        <option value="cancelled">Cancelled</option>
                    </select>
                </div>
            </div>

            <p id={subscriptionHelpId} className="section-help" style={{ margin: '12px 20px' }}>
                Subscription status does not include formal holds. Use Formal holds to inspect account access and enforcement evidence.
            </p>

            {/* Tenants Table */}
            <div className="table-container">
                {loading ? (
                    <div className="table-loading">
                        <div className="loading-spinner"></div>
                        <p>Loading tenants...</p>
                    </div>
                ) : readError ? <p>Tenant list unavailable. Use Retry to reload.</p> : sortedTenants.length === 0 ? (
                    <div className="empty-state">
                        <Building2 size={48} />
                        <h3>No tenants found</h3>
                        <p>Try adjusting your search or filter criteria.</p>
                    </div>
                ) : (
                    <table className="data-table">
                        <caption className="section-help" style={{ textAlign: 'left', padding: '8px 20px' }}>
                            Sorting applies to tenants on this page. Search and subscription filters apply across all pages.
                        </caption>
                        <thead>
                            <tr>
                                <th aria-sort={sortField === 'business_name' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    <button onClick={() => handleSort('business_name')}>Tenant Name {renderSortIcon('business_name')}</button>
                                </th>
                                <th>Plan</th>
                                <th aria-sort={sortField === 'status' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    <button onClick={() => handleSort('status')}>Subscription status {renderSortIcon('status')}</button>
                                </th>
                                <th aria-sort={sortField === 'minutes_used' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    <button onClick={() => handleSort('minutes_used')}>Usage {renderSortIcon('minutes_used')}</button>
                                </th>
                                <th aria-sort={sortField === 'user_count' ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    <button onClick={() => handleSort('user_count')}>Users {renderSortIcon('user_count')}</button>
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
                                                aria-label={`Actions for ${tenant.business_name}`}
                                                aria-expanded={activeMenu === tenant.id}
                                                onClick={() => setActiveMenu(activeMenu === tenant.id ? null : tenant.id)}
                                            >
                                                <MoreVertical size={16} />
                                            </button>
                                            {activeMenu === tenant.id && (
                                                <div className="action-menu">
                                                    {onFormalHolds && <button className="action-menu-item" onClick={(event) => {
                                                        event.currentTarget.closest('.actions-cell')?.querySelector<HTMLButtonElement>('.action-menu-btn')?.focus();
                                                        setActiveMenu(null); onFormalHolds(tenant);
                                                    }}>Formal holds</button>}
                                                    {tenant.status === 'active' || tenant.status === 'trialing' ? (
                                                        <button
                                                            className="action-menu-item danger"
                                                            onClick={(event) => {
                                                                event.currentTarget.closest('.actions-cell')?.querySelector<HTMLButtonElement>('.action-menu-btn')?.focus();
                                                                setActionError(null);
                                                                setConfirmAction({
                                                                    open: true,
                                                                    tenant,
                                                                    action: 'suspend'
                                                                });
                                                                setActiveMenu(null);
                                                            }}
                                                        >
                                                            <Pause size={14} />
                                                            Suspend subscription
                                                        </button>
                                                    ) : (
                                                        <button
                                                            className="action-menu-item"
                                                            onClick={(event) => {
                                                                event.currentTarget.closest('.actions-cell')?.querySelector<HTMLButtonElement>('.action-menu-btn')?.focus();
                                                                setActionError(null);
                                                                setConfirmAction({
                                                                    open: true,
                                                                    tenant,
                                                                    action: 'resume'
                                                                });
                                                                setActiveMenu(null);
                                                            }}
                                                        >
                                                            <Play size={14} />
                                                            Resume subscription
                                                        </button>
                                                    )}
                                                    <button
                                                        className="action-menu-item"
                                                        onClick={(event) => {
                                                            event.currentTarget.closest('.actions-cell')?.querySelector<HTMLButtonElement>('.action-menu-btn')?.focus();
                                                            setActionError(null);
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

            <ConfirmationModal
                isOpen={confirmAction.open && Boolean(confirmAction.tenant)}
                title={confirmAction.action === 'suspend' ? 'Suspend subscription?' : 'Resume subscription?'}
                message={confirmAction.action === 'suspend'
                    ? `Suspend the subscription for "${confirmAction.tenant?.business_name}"? New calls will be blocked. Formal holds are separate.`
                    : `Set the subscription for "${confirmAction.tenant?.business_name}" to active? Formal holds and existing plan and quota limits still apply.`}
                confirmLabel={confirmAction.action === 'suspend' ? 'Suspend' : 'Resume'}
                onConfirm={() => void handleSuspendResume()} onCancel={closeConfirm}
                loading={actionLoading} error={actionError}
            />

            {/* Quota Modal */}
            {quotaModal.open && quotaModal.tenant && (
                <QuotaModal
                    tenant={quotaModal.tenant}
                    onClose={closeQuota}
                    onSave={handleQuotaUpdate}
                    loading={actionLoading}
                    error={actionError}
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
    error: string | null;
}

// An empty/garbled box is invalid, never silently coerced to 0 or 1.
function parseBounded(raw: string, min: number, max: number): number | null {
    if (!/^\d+$/.test(raw.trim())) return null;
    const value = Number(raw);
    return value >= min && value <= max ? value : null;
}

function QuotaModal({ tenant, onClose, onSave, loading, error }: QuotaModalProps) {
    const root = useRef<HTMLDivElement>(null);
    useDialogFocus(true, root, onClose, loading);
    const [minutes, setMinutes] = useState(String(tenant.minutes_allocated));
    const [concurrentCalls, setConcurrentCalls] = useState(String(tenant.max_concurrent_calls));

    const parsedMinutes = parseBounded(minutes, 0, 2147483647);
    const parsedConcurrent = parseBounded(concurrentCalls, 1, 1000);
    // Send only what the admin actually changed, so a stale snapshot of the
    // other field cannot overwrite a value changed elsewhere since the list loaded.
    const changes: QuotaUpdateRequest = { expected_updated_at: tenant.updated_at ?? null };
    if (parsedMinutes !== null && parsedMinutes !== tenant.minutes_allocated) {
        changes.minutes_allocated = parsedMinutes;
    }
    if (parsedConcurrent !== null && parsedConcurrent !== tenant.max_concurrent_calls) {
        changes.max_concurrent_calls = parsedConcurrent;
    }
    const canSave = tenant.updated_at !== undefined && parsedMinutes !== null && parsedConcurrent !== null && Object.keys(changes).length > 1;

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (!canSave || loading) return;
        onSave(changes);
    };

    return (
        <div className="modal-overlay" onClick={() => { if (!loading) onClose(); }}>
            <div className="modal-content quota-modal" role="dialog" aria-modal="true" aria-labelledby="quota-title" ref={root} tabIndex={-1} onClick={(e) => e.stopPropagation()}>
                <h3 id="quota-title" className="modal-title">Edit Quota - {tenant.business_name}</h3>
                {tenant.updated_at === undefined && <p role="alert">The tenant version is missing. Close this dialog and refresh the list.</p>}
                <form onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label htmlFor="minutes">Minutes Allocated</label>
                        <input
                            type="number"
                            id="minutes"
                            value={minutes}
                            onChange={(e) => setMinutes(e.target.value)}
                            min={0}
                            max={2147483647}
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
                            onChange={(e) => setConcurrentCalls(e.target.value)}
                            min={1}
                            max={1000}
                            className="form-input"
                        />
                    </div>
                    {error && (
                        <div className="error-banner" role="alert">
                            <p>{error}</p>
                        </div>
                    )}
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
                            disabled={loading || !canSave}
                        >
                            {loading ? 'Saving...' : 'Save Changes'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}
