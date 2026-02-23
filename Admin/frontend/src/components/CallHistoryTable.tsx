import { useState, useEffect, useCallback } from 'react';
import {
    Phone,
    Search,
    Filter,
    ChevronLeft,
    ChevronRight,
    Clock,
    Building2,
    CheckCircle,
    XCircle,
    PhoneOff
} from 'lucide-react';
import { api } from '../lib/api';
import type { CallHistoryItem, CallHistoryParams } from '../lib/api';

interface CallHistoryTableProps {
    onCallSelect: (callId: string) => void;
}

function formatDuration(seconds: number | null): string {
    if (seconds === null) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
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

function CallStatusBadge({ status, outcome }: { status: string; outcome: string | null }) {
    let className = 'call-status-badge ';
    let icon = null;

    switch (status) {
        case 'completed':
            className += 'status-completed';
            icon = <CheckCircle size={12} />;
            break;
        case 'failed':
        case 'no_answer':
        case 'busy':
            className += 'status-failed';
            icon = <XCircle size={12} />;
            break;
        case 'terminated':
            className += 'status-terminated';
            icon = <PhoneOff size={12} />;
            break;
        default:
            className += 'status-default';
    }

    return (
        <span className={className}>
            {icon}
            {outcome || status}
        </span>
    );
}

export function CallHistoryTable({ onCallSelect }: CallHistoryTableProps) {
    const [calls, setCalls] = useState<CallHistoryItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');

    // Debounce search
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search);
            setPage(1);
        }, 300);
        return () => clearTimeout(timer);
    }, [search]);

    const fetchCalls = useCallback(async () => {
        setLoading(true);
        try {
            const params: CallHistoryParams = {
                page,
                page_size: pageSize,
            };
            if (debouncedSearch) params.search = debouncedSearch;
            if (statusFilter) params.status = statusFilter;

            const response = await api.getCallHistory(params);
            if (response.data) {
                setCalls(response.data.items);
                setTotal(response.data.total);
            }
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch call history');
        } finally {
            setLoading(false);
        }
    }, [page, pageSize, debouncedSearch, statusFilter]);

    useEffect(() => {
        fetchCalls();
    }, [fetchCalls]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="call-history-container">
            {/* Toolbar */}
            <div className="table-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        type="text"
                        placeholder="Search by phone number..."
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
                        <option value="completed">Completed</option>
                        <option value="failed">Failed</option>
                        <option value="no_answer">No Answer</option>
                        <option value="busy">Busy</option>
                        <option value="terminated">Terminated</option>
                    </select>
                </div>
            </div>

            {/* Table */}
            <div className="table-container">
                {loading ? (
                    <div className="table-loading">
                        <div className="loading-spinner"></div>
                        <p>Loading call history...</p>
                    </div>
                ) : error ? (
                    <div className="error-banner">
                        <p>{error}</p>
                        <button onClick={fetchCalls}>Retry</button>
                    </div>
                ) : calls.length === 0 ? (
                    <div className="empty-state">
                        <Phone size={48} />
                        <h3>No Calls Found</h3>
                        <p>Try adjusting your search or filter criteria.</p>
                    </div>
                ) : (
                    <table className="data-table clickable-rows">
                        <thead>
                            <tr>
                                <th>Date/Time</th>
                                <th>Tenant</th>
                                <th>Phone Number</th>
                                <th>Campaign</th>
                                <th>Status</th>
                                <th>Duration</th>
                            </tr>
                        </thead>
                        <tbody>
                            {calls.map((call) => (
                                <tr
                                    key={call.id}
                                    onClick={() => onCallSelect(call.id)}
                                    className="clickable-row"
                                >
                                    <td>{formatDate(call.created_at)}</td>
                                    <td>
                                        <div className="tenant-name-cell">
                                            <Building2 size={14} />
                                            <span>{call.tenant_name}</span>
                                        </div>
                                    </td>
                                    <td className="phone-cell">{call.phone_number}</td>
                                    <td>{call.campaign_name || '-'}</td>
                                    <td>
                                        <CallStatusBadge status={call.status} outcome={call.outcome} />
                                    </td>
                                    <td>
                                        <div className="duration-cell">
                                            <Clock size={14} />
                                            <span>{formatDuration(call.duration_seconds)}</span>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Pagination */}
            {!loading && calls.length > 0 && (
                <div className="pagination">
                    <span className="pagination-info">
                        Showing {((page - 1) * pageSize) + 1} - {Math.min(page * pageSize, total)} of {total}
                    </span>
                    <div className="pagination-controls">
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setPage(p => Math.max(1, p - 1))}
                            disabled={page === 1}
                        >
                            <ChevronLeft size={16} />
                        </button>
                        <span className="page-indicator">Page {page} of {totalPages}</span>
                        <button
                            className="btn btn-secondary btn-sm"
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
