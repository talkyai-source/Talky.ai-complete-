import { useCallback, useEffect, useState } from 'react';
import {
    Building2,
    CalendarDays,
    ChevronDown,
    ChevronLeft,
    ChevronRight,
    ChevronUp,
    ExternalLink,
    Filter,
    Mic2,
    RefreshCw,
    RotateCcw,
    Search,
    Trash2,
} from 'lucide-react';

import { api } from '../lib/api';
import type { AdminFeedbackItem, AdminFeedbackParams, TenantListItem } from '../lib/api';
import { AdminMediaPlayer } from './AdminMediaPlayer';
import { ConfirmationModal } from './ConfirmationModal';

interface FeedbackTableProps {
    onCallSelect: (callId: string) => void;
}

function formatBytes(value: number): string {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function FeedbackTable({ onCallSelect }: FeedbackTableProps) {
    const [items, setItems] = useState<AdminFeedbackItem[]>([]);
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const pageSize = 20;
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [status, setStatus] = useState('');
    const [tenantId, setTenantId] = useState('');
    const [fromDate, setFromDate] = useState('');
    const [toDate, setToDate] = useState('');
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [retryingId, setRetryingId] = useState<string | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<AdminFeedbackItem | null>(null);
    const [deleting, setDeleting] = useState(false);

    useEffect(() => {
        const timer = window.setTimeout(() => {
            setDebouncedSearch(search.trim());
            setPage(1);
        }, 300);
        return () => window.clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        api.getTenants().then((response) => {
            if (response.data) setTenants(response.data);
        });
    }, []);

    const fetchItems = useCallback(async () => {
        setLoading(true);
        setError(null);
        const params: AdminFeedbackParams = { page, page_size: pageSize };
        if (debouncedSearch) params.search = debouncedSearch;
        if (status) params.transcript_status = status;
        if (tenantId) params.tenant_id = tenantId;
        if (fromDate) params.from_date = fromDate;
        if (toDate) params.to_date = toDate;
        const response = await api.getAdminFeedback(params);
        if (response.error) {
            setError(response.error.message);
            setItems([]);
        } else if (response.data) {
            setItems(response.data.items);
            setTotal(response.data.total);
        }
        setLoading(false);
    }, [page, debouncedSearch, status, tenantId, fromDate, toDate]);

    useEffect(() => {
        // The request is intentionally tied to the complete filter snapshot.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        void fetchItems();
    }, [fetchItems]);

    const retryTranscription = async (item: AdminFeedbackItem) => {
        setRetryingId(item.id);
        setError(null);
        const response = await api.retryAdminFeedbackTranscription(item.id);
        if (response.error) {
            setError(response.error.message);
        } else if (response.data) {
            const updatedFeedback = response.data;
            setItems((current) => current.map((entry) => (
                entry.id === item.id ? updatedFeedback : entry
            )));
        }
        setRetryingId(null);
    };

    const deleteFeedback = async () => {
        if (!deleteTarget) return;
        setDeleting(true);
        const response = await api.deleteAdminFeedback(deleteTarget.id);
        if (response.error) {
            setError(response.error.message);
        } else {
            setDeleteTarget(null);
            await fetchItems();
        }
        setDeleting(false);
    };

    const resetFilters = () => {
        setSearch('');
        setStatus('');
        setTenantId('');
        setFromDate('');
        setToDate('');
        setPage(1);
    };

    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const filtersActive = Boolean(search || status || tenantId || fromDate || toDate);

    return (
        <div className="call-history-container">
            <div className="table-toolbar media-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        className="search-input"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder="Phone, call ID, tenant, or reviewer…"
                    />
                </div>
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        className="filter-select"
                        value={status}
                        onChange={(event) => {
                            setStatus(event.target.value);
                            setPage(1);
                        }}
                    >
                        <option value="">All transcript states</option>
                        <option value="done">Done</option>
                        <option value="pending">Pending</option>
                        <option value="failed">Failed</option>
                    </select>
                </div>
                <select
                    className="filter-select"
                    value={tenantId}
                    onChange={(event) => {
                        setTenantId(event.target.value);
                        setPage(1);
                    }}
                    aria-label="Filter feedback by tenant"
                >
                    <option value="">All tenants</option>
                    {tenants.map((tenant) => (
                        <option key={tenant.id} value={tenant.id}>{tenant.business_name}</option>
                    ))}
                </select>
                <div className="date-filter-group">
                    <CalendarDays size={16} />
                    <input
                        type="date"
                        className="filter-select"
                        value={fromDate}
                        max={toDate || undefined}
                        onChange={(event) => {
                            setFromDate(event.target.value);
                            setPage(1);
                        }}
                        aria-label="Feedback from date"
                    />
                    <span>to</span>
                    <input
                        type="date"
                        className="filter-select"
                        value={toDate}
                        min={fromDate || undefined}
                        onChange={(event) => {
                            setToDate(event.target.value);
                            setPage(1);
                        }}
                        aria-label="Feedback to date"
                    />
                </div>
                {filtersActive && (
                    <button className="btn btn-secondary btn-sm" onClick={resetFilters}>
                        <RotateCcw size={14} /> Reset
                    </button>
                )}
            </div>

            {error && (
                <div className="error-banner">
                    <p>{error}</p>
                    <button onClick={() => void fetchItems()}>Retry</button>
                </div>
            )}

            <div className="table-container">
                {loading ? (
                    <div className="table-loading">
                        <div className="loading-spinner"></div>
                        <p>Loading feedback notes…</p>
                    </div>
                ) : items.length === 0 ? (
                    <div className="empty-state">
                        <Mic2 size={48} />
                        <h3>No feedback notes found</h3>
                        <p>Voice-review notes will appear here after reviewers submit them.</p>
                    </div>
                ) : (
                    <table className="data-table admin-media-table feedback-table">
                        <thead>
                            <tr>
                                <th>Created</th>
                                <th>Tenant / Call</th>
                                <th>Reviewer</th>
                                <th>Voice Note</th>
                                <th>Transcript</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((item) => (
                                <tr key={item.id}>
                                    <td>{formatDate(item.created_at)}</td>
                                    <td>
                                        <div className="stacked-cell">
                                            <span><Building2 size={13} /> {item.tenant_name}</span>
                                            <strong>{item.phone_number || 'Unknown number'}</strong>
                                            <button className="link-button" onClick={() => onCallSelect(item.call_id)}>
                                                {item.call_id.slice(0, 8)}… <ExternalLink size={11} />
                                            </button>
                                        </div>
                                    </td>
                                    <td>
                                        <div className="stacked-cell">
                                            <strong>{item.created_by_name || 'Unknown reviewer'}</strong>
                                            <span>{item.created_by_email || '—'}</span>
                                        </div>
                                    </td>
                                    <td>
                                        <AdminMediaPlayer
                                            compact
                                            filename={`feedback-${item.call_id}.webm`}
                                            load={() => api.getAdminFeedbackAudio(item.id)}
                                        />
                                        <span className="storage-kind">
                                            {formatBytes(item.audio_size_bytes)} · attempt {item.transcription_attempts}
                                        </span>
                                    </td>
                                    <td className="feedback-transcript-cell">
                                        <span className={`call-status-badge status-${item.transcript_status}`}>
                                            {item.transcript_status}
                                        </span>
                                        {item.transcript ? (
                                            <>
                                                <p className={expandedId === item.id ? 'expanded' : ''}>{item.transcript}</p>
                                                {item.transcript.length > 120 && (
                                                    <button
                                                        className="link-button"
                                                        onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}
                                                    >
                                                        {expandedId === item.id ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                                        {expandedId === item.id ? 'Show less' : 'Read all'}
                                                    </button>
                                                )}
                                            </>
                                        ) : item.transcript_error ? (
                                            <p className="transcript-error">{item.transcript_error}</p>
                                        ) : (
                                            <p className="no-data-inline">No transcript yet</p>
                                        )}
                                    </td>
                                    <td>
                                        <div className="row-actions vertical">
                                            {(item.retryable || item.transcript_status === 'pending') && (
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    disabled={retryingId === item.id}
                                                    onClick={() => void retryTranscription(item)}
                                                >
                                                    <RefreshCw size={14} className={retryingId === item.id ? 'spinning' : ''} />
                                                    Retry transcript
                                                </button>
                                            )}
                                            <button className="btn btn-secondary btn-sm" onClick={() => onCallSelect(item.call_id)}>
                                                <ExternalLink size={14} /> Call
                                            </button>
                                            <button className="btn btn-danger btn-sm" onClick={() => setDeleteTarget(item)}>
                                                <Trash2 size={14} /> Delete
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {!loading && total > 0 && (
                <div className="pagination">
                    <span className="pagination-info">
                        Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
                    </span>
                    <div className="pagination-controls">
                        <button className="btn btn-secondary btn-sm" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>
                            <ChevronLeft size={16} />
                        </button>
                        <span className="page-indicator">Page {page} of {totalPages}</span>
                        <button className="btn btn-secondary btn-sm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>
                            <ChevronRight size={16} />
                        </button>
                    </div>
                </div>
            )}

            <ConfirmationModal
                isOpen={Boolean(deleteTarget)}
                title="Permanently delete feedback note?"
                message="This removes the reviewer audio and transcript permanently. This action cannot be undone."
                confirmLabel="Delete feedback"
                variant="danger"
                loading={deleting}
                onConfirm={() => void deleteFeedback()}
                onCancel={() => setDeleteTarget(null)}
            />
        </div>
    );
}
