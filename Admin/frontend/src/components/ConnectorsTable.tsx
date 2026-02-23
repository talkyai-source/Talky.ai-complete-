import { useState, useEffect, useCallback } from 'react';
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

interface ConnectorsTableProps {
    onSelectConnector?: (connector: AdminConnectorItem) => void;
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
    const c = config[status as keyof typeof config] || config.pending;
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

export function ConnectorsTable({ onSelectConnector }: ConnectorsTableProps) {
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

    const fetchConnectors = useCallback(async () => {
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
            if (response.data) {
                setConnectors(response.data.items);
                setTotal(response.data.total);
            }
        } catch (error) {
            console.error('Failed to fetch connectors:', error);
        } finally {
            setLoading(false);
        }
    }, [page, pageSize, statusFilter, typeFilter, providerFilter]);


    useEffect(() => {
        fetchConnectors();
    }, [fetchConnectors]);

    const handleReconnect = async (e: React.MouseEvent, connectorId: string) => {
        e.stopPropagation();
        try {
            await api.forceReconnect(connectorId);
            fetchConnectors();
        } catch (error) {
            console.error('Failed to reconnect:', error);
        }
    };

    const handleRevokeClick = (e: React.MouseEvent, connectorId: string) => {
        e.stopPropagation();
        setRevokeTarget(connectorId);
    };

    const handleRevokeConfirm = async () => {
        if (!revokeTarget) return;
        setRevokeLoading(true);
        try {
            await api.revokeConnector(revokeTarget);
            setRevokeTarget(null);
            fetchConnectors();
        } catch (error) {
            console.error('Failed to revoke:', error);
        } finally {
            setRevokeLoading(false);
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
                        type="text"
                        placeholder="Search connectors..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="search-input"
                    />
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
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
                <button className="btn btn-secondary btn-sm" onClick={fetchConnectors}>
                    <RefreshCw size={14} />
                    Refresh
                </button>
            </div>

            {/* Table */}
            <div className="table-container">
                {loading ? (
                    <div className="loading-state">
                        <Loader2 className="spinner" size={24} />
                        <span>Loading connectors...</span>
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
                                    <td><TokenStatusBadge status={connector.token_status} /></td>
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
                                                onClick={(e) => { e.stopPropagation(); onSelectConnector?.(connector); }}
                                            >
                                                <Eye size={14} />
                                            </button>
                                            {connector.status === 'active' && connector.token_status !== 'valid' && (
                                                <button
                                                    className="btn btn-icon btn-sm btn-warning"
                                                    title="Force Reconnect"
                                                    onClick={(e) => handleReconnect(e, connector.id)}
                                                >
                                                    <Zap size={14} />
                                                </button>
                                            )}
                                            {connector.status !== 'disconnected' && (
                                                <button
                                                    className="btn btn-icon btn-sm btn-danger"
                                                    title="Revoke Access"
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
            {!loading && totalPages > 1 && (
                <div className="table-footer">
                    <div className="pagination-info">
                        Showing {((page - 1) * pageSize) + 1} - {Math.min(page * pageSize, total)} of {total}
                    </div>
                    <div className="pagination-controls">
                        <button
                            className="btn btn-icon btn-sm"
                            disabled={page === 1}
                            onClick={() => setPage(p => p - 1)}
                        >
                            <ChevronLeft size={16} />
                        </button>
                        <span className="page-info">Page {page} of {totalPages}</span>
                        <button
                            className="btn btn-icon btn-sm"
                            disabled={page === totalPages}
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
                message="Are you sure you want to revoke this connector? This will disconnect the integration."
                confirmLabel="Revoke"
                cancelLabel="Cancel"
                variant="danger"
                onConfirm={handleRevokeConfirm}
                onCancel={() => setRevokeTarget(null)}
                loading={revokeLoading}
            />
        </div>
    );
}
