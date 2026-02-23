import { useState, useEffect, useCallback } from 'react';
import {
    Mail,
    MessageSquare,
    Phone,
    Calendar,
    Bell,
    Play,
    Search,
    Filter,
    ChevronLeft,
    ChevronRight,
    Loader2,
    RefreshCw,
    CheckCircle,
    XCircle,
    Ban,
    Building2,
    Clock,
    Zap,
    User
} from 'lucide-react';
import { api } from '../lib/api';
import type { ActionItem, ActionListParams, ActionType, ActionStatus } from '../lib/api';

interface ActionsTableProps {
    onActionSelect: (actionId: string) => void;
}

const ACTION_TYPE_CONFIG: Record<ActionType, { icon: typeof Mail; label: string }> = {
    'send_email': { icon: Mail, label: 'Email' },
    'send_sms': { icon: MessageSquare, label: 'SMS' },
    'initiate_call': { icon: Phone, label: 'Call' },
    'book_meeting': { icon: Calendar, label: 'Meeting' },
    'set_reminder': { icon: Bell, label: 'Reminder' },
    'start_campaign': { icon: Play, label: 'Campaign' }
};

function ActionStatusBadge({ status }: { status: ActionStatus }) {
    const config: Record<ActionStatus, { icon: typeof CheckCircle; className: string; label: string }> = {
        'pending': { icon: Loader2, className: 'status-pending', label: 'Pending' },
        'running': { icon: RefreshCw, className: 'status-running', label: 'Running' },
        'completed': { icon: CheckCircle, className: 'status-completed', label: 'Completed' },
        'failed': { icon: XCircle, className: 'status-failed', label: 'Failed' },
        'cancelled': { icon: Ban, className: 'status-cancelled', label: 'Cancelled' }
    };

    const { icon: Icon, className, label } = config[status] || config['pending'];
    const isSpinning = status === 'running' || status === 'pending';

    return (
        <span className={`action-status-badge ${className}`}>
            <Icon size={12} className={isSpinning ? 'spinning' : ''} />
            {label}
        </span>
    );
}

function ActionTypeBadge({ type }: { type: ActionType }) {
    const config = ACTION_TYPE_CONFIG[type] || { icon: Play, label: type };
    const Icon = config.icon;

    return (
        <span className="action-type-badge">
            <Icon size={14} />
            {config.label}
        </span>
    );
}

function formatDate(dateStr: string | null): string {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return dateStr;
    }
}

function formatDuration(ms: number | null): string {
    if (ms === null) return '-';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

export function ActionsTable({ onActionSelect }: ActionsTableProps) {
    const [actions, setActions] = useState<ActionItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [typeFilter, setTypeFilter] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');

    // Debounce search
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search);
            setPage(1);
        }, 300);
        return () => clearTimeout(timer);
    }, [search]);

    const fetchActions = useCallback(async () => {
        setLoading(true);
        try {
            const params: ActionListParams = {
                page,
                page_size: pageSize,
            };
            if (debouncedSearch) params.search = debouncedSearch;
            if (statusFilter) params.status = statusFilter;
            if (typeFilter) params.type = typeFilter;

            const response = await api.getActions(params);
            if (response.data) {
                setActions(response.data.items);
                setTotal(response.data.total);
            }
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch actions');
        } finally {
            setLoading(false);
        }
    }, [page, pageSize, debouncedSearch, statusFilter, typeFilter]);

    useEffect(() => {
        fetchActions();
    }, [fetchActions]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="actions-table-container">
            {/* Toolbar */}
            <div className="table-toolbar">
                <div className="search-box">
                    <Search size={16} />
                    <input
                        type="text"
                        placeholder="Search actions..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="search-input"
                    />
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        value={statusFilter}
                        onChange={(e) => {
                            setStatusFilter(e.target.value);
                            setPage(1);
                        }}
                        className="filter-select"
                    >
                        <option value="">All Statuses</option>
                        <option value="pending">Pending</option>
                        <option value="running">Running</option>
                        <option value="completed">Completed</option>
                        <option value="failed">Failed</option>
                        <option value="cancelled">Cancelled</option>
                    </select>
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        value={typeFilter}
                        onChange={(e) => {
                            setTypeFilter(e.target.value);
                            setPage(1);
                        }}
                        className="filter-select"
                    >
                        <option value="">All Types</option>
                        <option value="send_email">Email</option>
                        <option value="send_sms">SMS</option>
                        <option value="initiate_call">Call</option>
                        <option value="book_meeting">Meeting</option>
                        <option value="set_reminder">Reminder</option>
                        <option value="start_campaign">Campaign</option>
                    </select>
                </div>
            </div>

            {/* Table */}
            <div className="table-container">
                {loading ? (
                    <div className="table-loading">
                        <div className="loading-spinner"></div>
                        <p>Loading actions...</p>
                    </div>
                ) : error ? (
                    <div className="error-banner">
                        <p>{error}</p>
                        <button onClick={fetchActions}>Retry</button>
                    </div>
                ) : actions.length === 0 ? (
                    <div className="empty-state">
                        <Zap size={48} strokeWidth={1} />
                        <h3>No Actions Found</h3>
                        <p>No assistant activities were found matching your current filters.</p>
                    </div>
                ) : (
                    <table className="data-table clickable-rows">
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Tenant</th>
                                <th>Type</th>
                                <th>Lead</th>
                                <th>Status</th>
                                <th>Duration</th>
                                <th>Trigger</th>
                            </tr>
                        </thead>
                        <tbody>
                            {actions.map((action) => (
                                <tr
                                    key={action.id}
                                    onClick={() => onActionSelect(action.id)}
                                    className="clickable-row"
                                >
                                    <td>{formatDate(action.created_at)}</td>
                                    <td>
                                        <div className="tenant-name-cell">
                                            <Building2 size={14} />
                                            <span>{action.tenant_name}</span>
                                        </div>
                                    </td>
                                    <td><ActionTypeBadge type={action.type} /></td>
                                    <td>
                                        <div className="lead-cell">
                                            <User size={14} />
                                            <span>{action.lead_name || action.lead_phone || '-'}</span>
                                        </div>
                                    </td>
                                    <td><ActionStatusBadge status={action.status} /></td>
                                    <td>
                                        <div className="duration-cell">
                                            <Clock size={14} />
                                            <span>{formatDuration(action.duration_ms)}</span>
                                        </div>
                                    </td>
                                    <td className="trigger-cell">{action.triggered_by || '-'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Pagination */}
            {!loading && actions.length > 0 && (
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
                        <span className="page-indicator">Page {page} of {totalPages}</span>
                        <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                            disabled={page >= totalPages}
                        >
                            <ChevronRight size={16} />
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
