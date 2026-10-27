import { useState, useEffect, useCallback, useRef } from 'react';
import {
    Link2,
    Calendar,
    Mail,
    Database,
    Cloud,
    Search,
    Filter,
    ChevronLeft,
    ChevronRight,
    Loader2,
    RefreshCw,
    CheckCircle,
    XCircle,
    AlertTriangle,
    Clock,
    Building2,
    Eye,
    Zap,
    Ban
} from 'lucide-react';
import { api } from '../lib/api';
import type { AdminConnectorItem, ConnectorListParams } from '../lib/api';
import { ConfirmationModal } from './ConfirmationModal';
import { useAuthGeneration } from '../lib/use-auth-generation';

interface ConnectorsTableProps {
    onSelectConnector?: (connector: AdminConnectorItem) => void;
    onChanged?: () => void;
    refreshVersion?: number;
}

const providerIcons: Record<string, React.ReactNode> = {
    google_calendar: <Calendar size={16} />,
    outlook_calendar: <Calendar size={16} />,
    gmail: <Mail size={16} />,
    hubspot: <Database size={16} />,
    google_drive: <Cloud size={16} />,
};

const typeLabels: Record<string, string> = {
    calendar: 'Calendar',
    email: 'Email',
    crm: 'CRM',
    drive: 'Drive',
};

const providerLabels: Record<string, string> = {
    google_calendar: 'Google Calendar',
    outlook_calendar: 'Outlook',
    gmail: 'Gmail',
    hubspot: 'HubSpot',
    google_drive: 'Google Drive',
};

function TokenStatusBadge({ status }: { status: string }) {
    const config = {
        valid: { icon: <CheckCircle size={12} />, label: 'Valid', className: 'badge-success' },
        expiring_soon: { icon: <AlertTriangle size={12} />, label: 'Expiring', className: 'badge-warning' },
        expired: { icon: <XCircle size={12} />, label: 'Expired', className: 'badge-danger' },
        unknown: { icon: <Clock size={12} />, label: 'Unknown', className: 'badge-default' },
    };
    const c = config[status as keyof typeof config] || config.unknown;
    return <span className={`badge ${c.className}`}>{c.icon} {c.label}</span>;
}

function ConnectorStatusBadge({ status }: { status: string }) {
    const config = {
        active: { icon: <CheckCircle size={12} />, label: 'Active', className: 'badge-success' },
        pending: { icon: <Clock size={12} />, label: 'Pending', className: 'badge-warning' },
        error: { icon: <XCircle size={12} />, label: 'Error', className: 'badge-danger' },
        expired: { icon: <AlertTriangle size={12} />, label: 'Expired', className: 'badge-warning' },
        disconnected: { icon: <Ban size={12} />, label: 'Disconnected', className: 'badge-default' },
    };
    const c = config[status as keyof typeof config] || { icon: <Clock size={12} />, label: 'Unknown', className: 'badge-default' };
    return <span className={`badge ${c.className}`}>{c.icon} {c.label}</span>;
}

function ProviderBadge({ provider }: { provider: string }) {
    const icon = providerIcons[provider] || <Link2 size={16} />;
    const label = providerLabels[provider] || provider;
    return (
        <span className="badge badge-outline">
            {icon} {label}
        </span>
    );
}

export function ConnectorsTable(props: ConnectorsTableProps) {
    const generation = useAuthGeneration();
    return <OwnedConnectorsTable key={generation} {...props} generation={generation} />;
}

function OwnedConnectorsTable({ onSelectConnector, onChanged, refreshVersion, generation }: ConnectorsTableProps & { generation: number }) {
    const [connectors, setConnectors] = useState<AdminConnectorItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [typeFilter, setTypeFilter] = useState('');
    const [providerFilter, setProviderFilter] = useState('');
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [revokeTarget, setRevokeTarget] = useState<string | null>(null);
    const [revokeLoading, setRevokeLoading] = useState(false);
    // The API client never throws; failures come back as { error }.
    const [listError, setListError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [mutating, setMutating] = useState(false);
    const [loadedQuery, setLoadedQuery] = useState('');
    const query = JSON.stringify([page, statusFilter, typeFilter, providerFilter, refreshVersion]);
    const mounted = useRef(false);
    const sequence = useRef(0);
    const mutation = useRef(false);
    const searchInput = useRef<HTMLInputElement>(null);
    const owns = useCallback(() => mounted.current && generation === api.getAuthGeneration(), [generation]);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; sequence.current += 1; };
    }, []);

    const fetchConnectors = useCallback(async () => {
        if (!owns()) return;
        const request = ++sequence.current;
        const current = () => owns() && sequence.current === request;
        setLoading(true);
        try {
            const params: ConnectorListParams = {
                page,
                page_size: pageSize,
            };
            if (statusFilter) params.status = statusFilter;
            if (typeFilter) params.type = typeFilter;
            if (providerFilter) params.provider = providerFilter;

            const response = await api.getConnectors(params);
            if (!current()) return;
            if (response.error) throw new Error(response.error.message);
            if (!response.data || !Array.isArray(response.data.items) || !Number.isSafeInteger(response.data.total) || response.data.total < 0
                || response.data.items.some(item => !item.id || !item.tenant_id)) throw new Error('The connector list response is incomplete.');
            setListError(null);
            const lastPage = Math.max(1, Math.ceil(response.data.total / pageSize));
            if (page > lastPage) {
                // This current response proves the page no longer exists. The query effect
                // reads the last valid page without repeating the confirmed mutation.
                setConnectors([]); setLoadedQuery(''); setPage(lastPage);
                return;
            }
            setConnectors(response.data.items);
            setTotal(response.data.total);
            setLoadedQuery(query);
        } catch (error) {
            if (current()) {
                setListError(error instanceof Error ? error.message : 'Failed to load connectors.');
                setConnectors([]); setTotal(0); setLoadedQuery(query);
            }
        } finally {
            if (current()) setLoading(false);
        }
    }, [page, pageSize, statusFilter, typeFilter, providerFilter, owns, query]);

    const retryInventory = (trigger: HTMLButtonElement) => {
        if (!owns() || !trigger.isConnected || loading || loadedQuery !== query || !listError) return;
        // Loading removes Retry; preserve only the current invoking control's focus.
        if (document.activeElement === trigger) searchInput.current?.focus();
        void fetchConnectors();
    };

    useEffect(() => {
        fetchConnectors();
        return () => { sequence.current += 1; };
    }, [fetchConnectors]);

    const handleReconnect = async (e: React.MouseEvent, connectorId: string) => {
        e.stopPropagation();
        if (!owns() || mutation.current || loading || loadedQuery !== query) return;
        mutation.current = true; setMutating(true);
        setActionError(null);
        setNotice(null);
        try {
            const res = await api.forceReconnect(connectorId);
            if (!owns()) return;
            if (res.error) throw new Error(res.error.message);
            if (!res.data?.success || res.data.connector_id !== connectorId) throw new Error('Token refresh was not confirmed. Refresh the list to check its state.');
            setNotice('Token refresh confirmed.');
            onChanged?.();
        } catch (error) {
            if (owns()) setActionError(error instanceof Error ? error.message : 'Token refresh was not confirmed. Refresh the list to check its state.');
        } finally {
            if (owns()) { mutation.current = false; setMutating(false); void fetchConnectors(); }
        }
    };

    const handleRevokeClick = (e: React.MouseEvent, connectorId: string) => {
        e.stopPropagation();
        if (!owns() || mutation.current || loading || loadedQuery !== query) return;
        setActionError(null);
        setRevokeTarget(connectorId);
    };

    const handleRevokeConfirm = async () => {
        if (!revokeTarget || !owns() || mutation.current) return;
        mutation.current = true; setMutating(true);
        setRevokeLoading(true);
        setActionError(null);
        try {
            const res = await api.revokeConnector(revokeTarget);
            if (!owns()) return;
            if (res.error) throw new Error(res.error.message);
            if (!res.data?.success || res.data.connector_id !== revokeTarget) throw new Error('Disconnection was not confirmed. Refresh the list to check its state.');
            setNotice(res.data.provider_revoked === true && res.data.provider_revocation_status === 'confirmed'
                ? 'Disabled locally. Provider access revocation confirmed.'
                : 'Disabled locally. Provider access revocation is unconfirmed; access at the provider may remain.');
            setRevokeTarget(null);
            onChanged?.();
            void fetchConnectors();
        } catch (error) {
            if (owns()) setActionError(error instanceof Error ? error.message : 'Disconnection was not confirmed. Refresh the list to check its state.');
        } finally {
            if (owns()) { mutation.current = false; setMutating(false); setRevokeLoading(false); }
        }
    };

    const filteredConnectors = connectors.filter(c => {
        if (!search) return true;
        const searchLower = search.toLowerCase();
        return (
            c.tenant_name.toLowerCase().includes(searchLower) ||
            c.name?.toLowerCase().includes(searchLower) ||
            c.account_email?.toLowerCase().includes(searchLower) ||
            c.provider.toLowerCase().includes(searchLower)
        );
    });

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="card">
            {/* Toolbar */}
            <div className="table-toolbar">
                <div className="search-box">
                    <Search size={16} />
                    <input
                        ref={searchInput}
                        type="text"
                        placeholder="Search this page..."
                        aria-label="Search connectors on this page"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="search-input"
                    />
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        aria-label="Connector status" disabled={mutating || revokeTarget !== null}
                        value={statusFilter}
                        onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
                        className="filter-select"
                    >
                        <option value="">All Statuses</option>
                        <option value="active">Active</option>
                        <option value="pending">Pending</option>
                        <option value="error">Error</option>
                        <option value="expired">Expired</option>
                        <option value="disconnected">Disconnected</option>
                    </select>
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        aria-label="Connector type" disabled={mutating || revokeTarget !== null}
                        value={typeFilter}
                        onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}
                        className="filter-select"
                    >
                        <option value="">All Types</option>
                        <option value="calendar">Calendar</option>
                        <option value="email">Email</option>
                        <option value="crm">CRM</option>
                        <option value="drive">Drive</option>
                    </select>
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        aria-label="Connector provider" disabled={mutating || revokeTarget !== null}
                        value={providerFilter}
                        onChange={(e) => { setProviderFilter(e.target.value); setPage(1); }}
                        className="filter-select"
                    >
                        <option value="">All Providers</option>
                        <option value="google_calendar">Google Calendar</option>
                        <option value="outlook_calendar">Outlook</option>
                        <option value="gmail">Gmail</option>
                        <option value="hubspot">HubSpot</option>
                        <option value="google_drive">Google Drive</option>
                    </select>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={fetchConnectors} disabled={mutating || revokeTarget !== null}>
                    <RefreshCw size={14} />
                    Refresh
                </button>
            </div>

            {notice && <div className="info-banner" role="status">{notice}</div>}
            {search && <p className="pagination-info">Search matches only the currently loaded page; use filters and pagination to inspect other connectors.</p>}

            {actionError && revokeTarget === null && (
                <div className="error-banner" role="alert">
                    <p>{actionError}</p>
                </div>
            )}

            {/* Table */}
            <div className="table-container">
                {loading || loadedQuery !== query ? (
                    <div className="loading-state">
                        <Loader2 className="spinner" size={24} />
                        <span>Loading connectors...</span>
                    </div>
                ) : listError ? (
                    <div className="error-banner" role="alert">
                        <p>{listError}</p>
                        <button onClick={event => retryInventory(event.currentTarget)}>Retry</button>
                    </div>
                ) : filteredConnectors.length === 0 ? (
                    <div className="empty-state">
                        <Link2 size={48} />
                        <h3>No Connectors Found</h3>
                        <p>No connectors match your current filters.</p>
                    </div>
                ) : (
                    <table className="data-table clickable-rows">
                        <thead>
                            <tr>
                                <th>Tenant</th>
                                <th>Provider</th>
                                <th>Type</th>
                                <th>Account</th>
                                <th>Status</th>
                                <th>Token</th>
                                <th>Last Refresh</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {filteredConnectors.map((connector) => (
                                <tr
                                    key={connector.id}
                                    onClick={() => onSelectConnector?.(connector)}
                                    style={{ cursor: 'pointer' }}
                                >
                                    <td>
                                        <div className="tenant-name-cell">
                                            <Building2 size={14} />
                                            <span>{connector.tenant_name}</span>
                                        </div>
                                    </td>
                                    <td><ProviderBadge provider={connector.provider} /></td>
                                    <td>{typeLabels[connector.type] || connector.type}</td>
                                    <td>{connector.account_email || '-'}</td>
                                    <td><ConnectorStatusBadge status={connector.status} /></td>
                                    <td><TokenStatusBadge status={connector.token_status} />
                                        {connector.refresh_state === 'uncertain' && <span className="badge badge-warning">Refresh uncertain</span>}
                                        {connector.refresh_state === 'in_flight' && <span className="badge badge-warning">Refreshing</span>}
                                        {connector.provider_revocation_status === 'unconfirmed' && <span className="badge badge-warning">Provider revocation unconfirmed</span>}
                                    </td>
                                    <td>
                                        {connector.last_refreshed_at
                                            ? new Date(connector.last_refreshed_at).toLocaleString()
                                            : '-'}
                                    </td>
                                    <td>
                                        <div className="action-buttons">
                                            <button
                                                className="btn btn-icon btn-sm"
                                                title="View Details"
                                                aria-label={`View details for ${connector.name || connector.provider}`}
                                                onClick={(e) => { e.stopPropagation(); onSelectConnector?.(connector); }}
                                            >
                                                <Eye size={14} />
                                            </button>
                                            {connector.status === 'active' && connector.token_status !== 'valid' && (
                                                <button
                                                    className="btn btn-icon btn-sm btn-warning"
                                                    title="Refresh token"
                                                    aria-label={`Refresh token for ${connector.name || connector.provider}`}
                                                    disabled={mutating || connector.refresh_state !== 'idle'}
                                                    onClick={(e) => handleReconnect(e, connector.id)}
                                                >
                                                    <Zap size={14} />
                                                </button>
                                            )}
                                            {connector.status !== 'disconnected' && (
                                                <button
                                                    className="btn btn-icon btn-sm btn-danger"
                                                    title="Revoke Access"
                                                    aria-label={`Revoke access for ${connector.name || connector.provider}`}
                                                    disabled={mutating}
                                                    onClick={(e) => handleRevokeClick(e, connector.id)}
                                                >
                                                    <Ban size={14} />
                                                </button>
                                            )}
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Pagination */}
            {!loading && loadedQuery === query && !listError && totalPages > 1 && (
                <div className="table-footer">
                    <div className="pagination-info">
                        Showing {((page - 1) * pageSize) + 1} - {Math.min(page * pageSize, total)} of {total}
                    </div>
                    <div className="pagination-controls">
                        <button
                            className="btn btn-icon btn-sm"
                            aria-label="Previous connectors page" disabled={page === 1 || mutating || revokeTarget !== null}
                            onClick={() => setPage(p => p - 1)}
                        >
                            <ChevronLeft size={16} />
                        </button>
                        <span className="page-info">Page {page} of {totalPages}</span>
                        <button
                            className="btn btn-icon btn-sm"
                            aria-label="Next connectors page" disabled={page >= totalPages || mutating || revokeTarget !== null}
                            onClick={() => setPage(p => p + 1)}
                        >
                            <ChevronRight size={16} />
                        </button>
                    </div>
                </div>
            )}

            {/* Revoke Confirmation Modal */}
            <ConfirmationModal
                isOpen={revokeTarget !== null}
                title="Revoke Connector Access"
                message="Disable this integration locally and request provider access revocation? Provider confirmation will be shown separately."
                confirmLabel="Revoke"
                cancelLabel="Cancel"
                variant="danger"
                onConfirm={handleRevokeConfirm}
                onCancel={() => { setRevokeTarget(null); setActionError(null); }}
                loading={revokeLoading}
                error={revokeTarget !== null ? actionError : null}
                returnFocusFallback={searchInput}
            />
        </div>
    );
}
