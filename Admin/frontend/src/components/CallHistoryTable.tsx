import { useState, useEffect, useCallback, useRef } from 'react';
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
    PhoneOff,
    CalendarDays,
    Mic2,
    AudioLines,
    RotateCcw,
    PhoneIncoming,
    PhoneOutgoing,
} from 'lucide-react';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { TenantFilter } from './TenantFilter';
import { useOperationOwner } from '../lib/use-operation-owner';
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
        case 'ended':
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

export function CallHistoryTable(props: CallHistoryTableProps) {
    const generation = useAuthGeneration();
    return <OwnedCallHistoryTable key={generation} {...props} generation={generation} />;
}

function OwnedCallHistoryTable({ onCallSelect, generation }: CallHistoryTableProps & { generation: number }) {
    const [calls, setCalls] = useState<CallHistoryItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [tenantFilter, setTenantFilter] = useState('');
    const [directionFilter, setDirectionFilter] = useState('');
    const [fromDate, setFromDate] = useState('');
    const [toDate, setToDate] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');

    const query = JSON.stringify([page, debouncedSearch, statusFilter, tenantFilter, directionFilter, fromDate, toDate]);
    const capture = useOperationOwner(generation, query);
    const request = useRef(0);
    const [loadedQuery, setLoadedQuery] = useState('');

    // Debounce search
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search);
            setPage(1);
        }, 300);
        return () => clearTimeout(timer);
    }, [search]);

    const fetchCalls = useCallback(async () => {
        const owns = capture();
        if (!owns()) return;
        const sequence = ++request.current;
        const current = () => owns() && sequence === request.current;
        setLoading(true);
        try {
            const params: CallHistoryParams = {
                page,
                page_size: pageSize,
            };
            if (debouncedSearch) params.search = debouncedSearch;
            if (statusFilter) params.status = statusFilter;
            if (tenantFilter) params.tenant_id = tenantFilter;
            if (directionFilter === 'inbound' || directionFilter === 'outbound') {
                params.direction = directionFilter;
            }
            if (fromDate) params.from_date = fromDate;
            if (toDate) params.to_date = toDate;

            const response = await api.getCallHistory(params);
            if (!current()) return;
            setLoadedQuery(query);
            if (!response.error && (!response.data || !Array.isArray(response.data.items) || !Number.isSafeInteger(response.data.total))) throw new Error('The list response is incomplete.');
            if (response.error) throw new Error(response.error.message);
            if (response.data) {
                setCalls(response.data.items);
                setTotal(response.data.total);
            }
            setError(null);
        } catch (err) {
            if (!current()) return;
            setLoadedQuery(query);
            setError(err instanceof Error ? err.message : 'Failed to fetch call history');
        } finally {
            if (current()) setLoading(false);
        }
    }, [page, pageSize, debouncedSearch, statusFilter, tenantFilter, directionFilter, fromDate, toDate, capture, query]);

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
                        <option value="ended">Ended</option>
                        <option value="completed">Completed</option>
                        <option value="failed">Failed</option>
                        <option value="queued">Queued</option>
                        <option value="dialing">Dialing</option>
                        <option value="ringing">Ringing</option>
                        <option value="answered">Answered</option>
                        <option value="in_call">In Call</option>
                    </select>
                </div>
                <select
                    value={directionFilter}
                    onChange={(event) => {
                        setDirectionFilter(event.target.value);
                        setPage(1);
                    }}
                    className="filter-select"
                    aria-label="Filter by call direction"
                >
                    <option value="">All directions</option>
                    <option value="inbound">Inbound</option>
                    <option value="outbound">Outbound</option>
                </select>
                <TenantFilter includeArchived value={tenantFilter} onChange={value => { setTenantFilter(value); setPage(1); }} />
                <div className="date-filter-group">
                    <CalendarDays size={16} />
                    <input
                        type="date"
                        value={fromDate}
                        max={toDate || undefined}
                        onChange={(event) => {
                            setFromDate(event.target.value);
                            setPage(1);
                        }}
                        className="filter-select"
                        aria-label="Calls from date"
                    />
                    <span>to</span>
                    <input
                        type="date"
                        value={toDate}
                        min={fromDate || undefined}
                        onChange={(event) => {
                            setToDate(event.target.value);
                            setPage(1);
                        }}
                        className="filter-select"
                        aria-label="Calls to date"
                    />
                </div>
                {(statusFilter || tenantFilter || directionFilter || fromDate || toDate || search) && (
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                            setSearch('');
                            setStatusFilter('');
                            setTenantFilter('');
                            setDirectionFilter('');
                            setFromDate('');
                            setToDate('');
                            setPage(1);
                        }}
                    >
                        <RotateCcw size={14} />
                        Reset
                    </button>
                )}
            </div>

            {/* Table */}
            <div className="table-container">
                {loading || loadedQuery !== query ? (
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
                                <th>Direction / DID</th>
                                <th>Campaign</th>
                                <th>Status</th>
                                <th>Media &amp; Review</th>
                                <th>Duration</th>
                            </tr>
                        </thead>
                        <tbody>
                            {calls.map((call) => (
                                <tr
                                    key={call.id}
                                    onClick={() => { if (capture()()) onCallSelect(call.id); }}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter' || event.key === ' ') {
                                            event.preventDefault();
                                            if (capture()()) onCallSelect(call.id);
                                        }
                                    }}
                                    className="clickable-row"
                                    role="button"
                                    tabIndex={0}
                                    aria-label={`Open ${call.direction || 'unknown'} call ${call.id}`}
                                >
                                    <td>{formatDate(call.created_at)}</td>
                                    <td>
                                        <div className="tenant-name-cell">
                                            <Building2 size={14} />
                                            <span>{call.tenant_name}</span>
                                        </div>
                                    </td>
                                    <td className="phone-cell">{call.phone_number}</td>
                                    <td>
                                        <span className={`direction-badge direction-${call.direction || 'unknown'}`}>
                                            {call.direction === 'inbound' ? <PhoneIncoming size={13} /> : call.direction === 'outbound' ? <PhoneOutgoing size={13} /> : null}
                                            {call.direction || 'unknown'}
                                        </span>
                                        {call.called_did && <span className="called-did">DID {call.called_did}</span>}
                                    </td>
                                    <td>{call.campaign_name || '-'}</td>
                                    <td>
                                        <CallStatusBadge status={call.status} outcome={call.outcome} />
                                    </td>
                                    <td>
                                        <div className="media-indicators">
                                            {call.has_recording === true && (
                                                <span className="media-indicator" title="Call recording available">
                                                    <AudioLines size={13} /> Recording
                                                </span>
                                            )}
                                            {call.has_feedback === true && (
                                                <span
                                                    className={`media-indicator transcript-${call.feedback_transcript_status || 'pending'}`}
                                                    title={`Feedback transcription: ${call.feedback_transcript_status || 'pending'}`}
                                                >
                                                    <Mic2 size={13} /> Feedback
                                                </span>
                                            )}
                                            {call.has_recording !== true && call.has_recording !== false && (
                                                <span className="media-indicator" title="Recording metadata could not be checked">Recording status unavailable</span>
                                            )}
                                            {call.has_feedback !== true && call.has_feedback !== false && (
                                                <span className="media-indicator" title="Feedback metadata could not be checked">Feedback status unavailable</span>
                                            )}
                                            {call.has_recording === false && call.has_feedback === false && <span className="no-data-inline">—</span>}
                                        </div>
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
            {!loading && loadedQuery === query && !error && calls.length > 0 && (
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
