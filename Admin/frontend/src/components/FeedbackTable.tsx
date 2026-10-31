import { useCallback, useEffect, useState, useRef } from 'react';
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
    ShieldAlert,
    Trash2,
} from 'lucide-react';

import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { TenantFilter } from './TenantFilter';
import { useOperationOwner } from '../lib/use-operation-owner';
import type { AdminFeedbackItem, AdminFeedbackParams } from '../lib/api';
import { AdminMediaPlayer } from './AdminMediaPlayer';
import { ConfirmationModal } from './ConfirmationModal';
import { MediaDeletionHistory } from './MediaDeletionHistory';
import { mediaDeletionTarget, matchesMediaDeletionReceipt, recoveredMediaDeletion, matchesFeedbackRetry, type MediaDeletionNotice } from '../lib/media-deletion-contracts';

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

export function FeedbackTable(props: FeedbackTableProps) {
    const generation = useAuthGeneration();
    return <OwnedFeedbackTable key={generation} {...props} generation={generation} />;
}

function OwnedFeedbackTable({ onCallSelect, generation }: FeedbackTableProps & { generation: number }) {
    const [items, setItems] = useState<AdminFeedbackItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [total, setTotal] = useState(0);
    const pageSize = 20;
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [status, setStatus] = useState('');
    const [tenantId, setTenantId] = useState('');
    const [direction, setDirection] = useState('');
    const [fromDate, setFromDate] = useState('');
    const [toDate, setToDate] = useState('');
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [retryResult, setRetryResult] = useState<{ id: string; status: string; attempts: number } | null>(null);
    const [retryingId, setRetryingId] = useState<string | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<AdminFeedbackItem | null>(null);
    const [deleteReason, setDeleteReason] = useState('');
    const [reasonReadOnly, setReasonReadOnly] = useState(false);
    const submittedReason = useRef<string | null>(null);
    const [deleteIdempotencyKey, setDeleteIdempotencyKey] = useState('');
    const [deleting, setDeleting] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [deletionNotice, setDeletionNotice] = useState<MediaDeletionNotice | null>(null);
    const mutation = useRef(false);
    const searchInput = useRef<HTMLInputElement>(null);
    const [deleteQuery, setDeleteQuery] = useState('');

    const query = JSON.stringify([page, debouncedSearch, status, tenantId, direction, fromDate, toDate]);
    const capture = useOperationOwner(generation, query);
    const captureRecovery = useOperationOwner(generation, 'deletion-notice');
    const request = useRef(0);
    const [loadedQuery, setLoadedQuery] = useState('');

    useEffect(() => {
        const timer = window.setTimeout(() => {
            setDebouncedSearch(search.trim());
            setPage(1);
        }, 300);
        return () => window.clearTimeout(timer);
    }, [search]);



    useEffect(() => {
        mutation.current = false;
        setDeletionNotice(previous => previous?.state === 'pending'
            ? { ...previous, state: 'unconfirmed', message: 'The view changed before the deletion result was confirmed. Check the saved deletion.' } : previous);
        // The query owns both its rows and its pending confirmation state.
        setDeleteTarget(null);
        setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false); setDeleteIdempotencyKey(''); setDeleting(false); setActionError(null);
        setRetryingId(null);
    }, [capture]);

    const fetchItems = useCallback(async () => {
        const owns = capture();
        if (!owns()) return;
        const sequence = ++request.current;
        const current = () => owns() && sequence === request.current;
        setLoading(true);
        setError(null);
        const params: AdminFeedbackParams = { page, page_size: pageSize };
        if (debouncedSearch) params.search = debouncedSearch;
        if (status) params.transcript_status = status;
        if (tenantId) params.tenant_id = tenantId;
        if (direction === 'inbound' || direction === 'outbound') params.direction = direction;
        if (fromDate) params.from_date = fromDate;
        if (toDate) params.to_date = toDate;
        const response = await api.getAdminFeedback(params);
        if (!current()) return;
        setLoadedQuery(query);
        if (response.error) {
            setError(response.error.message);
            setItems([]); setTotal(0);
        } else if (response.data && Array.isArray(response.data.items) && Number.isSafeInteger(response.data.total) && response.data.total >= 0
            && response.data.items.every(item => item.id && item.call_id && item.tenant_id && (!tenantId || item.tenant_id === tenantId))) {
            const lastPage = Math.max(1, Math.ceil(response.data.total / pageSize));
            setTotal(response.data.total);
            if (page > lastPage) {
                // This owned response proves the requested page no longer exists.
                // The normal query effect reads the corrected page without repeating the mutation.
                setItems([]); setLoadedQuery(''); setPage(lastPage);
                return;
            }
            setItems(response.data.items);
        } else { setItems([]); setTotal(0); setError('The media list response is incomplete or belongs to another tenant.'); }
        setLoading(false);
    }, [page, debouncedSearch, status, tenantId, direction, fromDate, toDate, capture, query]);

    const retryInventory = (trigger: HTMLButtonElement) => {
        if (!capture()() || !trigger.isConnected || !error) return;
        // Clearing the error removes Retry immediately; hand off only its owned focus.
        if (document.activeElement === trigger) searchInput.current?.focus();
        void fetchItems();
    };

    useEffect(() => {
        // The request is intentionally tied to the complete filter snapshot.
        void fetchItems();
    }, [fetchItems]);

    const retryTranscription = async (item: AdminFeedbackItem, trigger?: HTMLButtonElement) => {
        const owns = capture();
        if (!owns() || mutation.current) return;
        mutation.current = true;
        const ownedFocus = Boolean(trigger && document.activeElement === trigger);
        setRetryingId(item.id); setActionError(null); setRetryResult(null);
        try {
            const response = await api.retryAdminFeedbackTranscription(item.id);
            if (!owns()) return;
            if (response.error) setActionError(response.error.message);
            else if (matchesFeedbackRetry(response.data, item)) {
                setRetryResult({ id: item.id, status: response.data.transcript_status, attempts: response.data.transcription_attempts });
                // Refresh removes the invoking row while loading. Preserve deliberate focus elsewhere.
                if (ownedFocus && (document.activeElement === trigger || document.activeElement === document.body)) {
                    searchInput.current?.focus();
                }
                // The saved result and this filtered inventory are separate observations.
                await fetchItems();
            } else setActionError('The transcription response did not match this feedback note. Refresh the list to check its state.');
        } catch (caught) {
            if (owns()) setActionError(caught instanceof Error ? caught.message : 'Transcription retry could not be confirmed.');
        } finally {
            if (owns()) { mutation.current = false; setRetryingId(null); }
        }
    };

    const deleteFeedback = async () => {
        const owns = capture();
        if (!owns() || mutation.current || deleteQuery !== query || !deleteTarget || !deleteIdempotencyKey) return;
        mutation.current = true;
        submittedReason.current ??= deleteReason.trim();
        const target = mediaDeletionTarget('call_feedback', deleteTarget);
        setDeletionNotice({ target, state: 'pending' });
        setReasonReadOnly(true);
        setDeleting(true); setActionError(null);
        try {
            const response = await api.deleteAdminFeedback(deleteTarget.id, submittedReason.current, deleteIdempotencyKey);
            if (!owns()) return;
            if (response.error || !matchesMediaDeletionReceipt(response.data, target)) {
                const message = response.error?.message ?? 'Deletion was not confirmed. Check the saved deletion or explicitly retry the same reviewed request.';
                setActionError(message);
                setDeletionNotice({ target, state: 'unconfirmed', message });
                return;
            }
            setDeletionNotice({ target, state: response.data.outcome, receipt: response.data });
            setDeleteTarget(null);
            setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false);
            setDeleteIdempotencyKey('');
            await fetchItems();
        } catch (caught) {
            if (owns()) {
                const message = caught instanceof Error ? caught.message : 'Deletion could not be confirmed. Check the saved deletion.';
                setActionError(message);
                setDeletionNotice(previous => previous?.state === 'completed' || previous?.state === 'absent'
                    ? previous : { target, state: 'unconfirmed', message });
            }
        } finally {
            if (owns()) { mutation.current = false; setDeleting(false); }
        }
    };

    const openDelete = (item: AdminFeedbackItem) => {
        if (!capture()() || mutation.current || item.legal_hold) return;
        setDeleteQuery(query); setActionError(null);
        const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        setDeleteTarget(item);
        setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false);
        setDeleteIdempotencyKey(`admin-media:feedback:${item.id}:${id}`);
    };

    const closeDelete = () => {
        if (mutation.current) return;
        setDeleteTarget(null);
        setDeleteReason(''); submittedReason.current = null; setReasonReadOnly(false);
        setDeleteIdempotencyKey('');
    };

    const resetFilters = () => {
        setSearch('');
        setStatus('');
        setTenantId('');
        setDirection('');
        setFromDate('');
        setToDate('');
        setPage(1);
    };

    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const filtersActive = Boolean(search || status || tenantId || direction || fromDate || toDate);

    return (
        <div className="call-history-container">
            {actionError && !deleteTarget && <p role="alert">{actionError}</p>}
            {retryResult && <p role="status">Saved transcription result for feedback {retryResult.id}: {retryResult.status}; attempt {retryResult.attempts}. The list is refreshed separately.</p>}
            <div className="table-toolbar media-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        className="search-input"
                        ref={searchInput}
                        aria-label="Search feedback notes"
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
                    value={direction}
                    onChange={(event) => { setDirection(event.target.value); setPage(1); }}
                    aria-label="Filter feedback by direction"
                >
                    <option value="">All directions</option>
                    <option value="inbound">Inbound</option>
                    <option value="outbound">Outbound</option>
                </select>
                <TenantFilter includeArchived value={tenantId} onChange={value => { setTenantId(value); setPage(1); }} />
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
                    <button onClick={event => retryInventory(event.currentTarget)}>Retry</button>
                </div>
            )}

            <div className="table-container">
                {loading || loadedQuery !== query ? (
                    <div className="table-loading">
                        <div className="loading-spinner"></div>
                        <p>Loading feedback notes…</p>
                    </div>
                ) : error ? null : items.length === 0 ? (
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
                                            <span className={`direction-badge direction-${item.direction || 'unknown'}`}>
                                                {item.direction || 'unknown'}{item.called_did ? ` · DID ${item.called_did}` : ''}
                                            </span>
                                            <button className="link-button" onClick={() => { if (capture()()) onCallSelect(item.call_id); }}>
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
                                            resourceId={`feedback:${item.id}`}
                                            compact
                                            filenameBase={`feedback-${item.call_id}`}
                                            mimeType={item.audio_mime_type}
                                            fallbackExtension="webm"
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
                                                    disabled={retryingId !== null || deleting}
                                                    onClick={event => void retryTranscription(item, event.currentTarget)}
                                                >
                                                    <RefreshCw size={14} className={retryingId === item.id ? 'spinning' : ''} />
                                                    Retry transcript
                                                </button>
                                            )}
                                            <button className="btn btn-secondary btn-sm" onClick={() => { if (capture()()) onCallSelect(item.call_id); }}>
                                                <ExternalLink size={14} /> Call
                                            </button>
                                            <button
                                                className="btn btn-danger btn-sm"
                                                onClick={() => openDelete(item)}
                                                disabled={item.legal_hold || deleting || retryingId !== null}
                                                title={item.legal_hold ? 'Deletion blocked by an active compliance/legal hold' : undefined}
                                            >
                                                {item.legal_hold ? <ShieldAlert size={14} /> : <Trash2 size={14} />}
                                                {item.legal_hold ? 'Legal hold' : 'Delete'}
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {!loading && loadedQuery === query && !error && total > 0 && (
                <div className="pagination">
                    <span className="pagination-info">
                        Showing {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
                    </span>
                    <div className="pagination-controls">
                        <button className="btn btn-secondary btn-sm" aria-label="Previous feedback page" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>
                            <ChevronLeft size={16} />
                        </button>
                        <span className="page-indicator">Page {page} of {totalPages}</span>
                        <button className="btn btn-secondary btn-sm" aria-label="Next feedback page" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>
                            <ChevronRight size={16} />
                        </button>
                    </div>
                </div>
            )}

            <MediaDeletionHistory resourceType="call_feedback" tenantId={tenantId || undefined} notice={deletionNotice}
                onRecovered={(expected, saved) => {
                    if (!captureRecovery()()) return;
                    setDeletionNotice(previous => previous === expected ? recoveredMediaDeletion(previous, saved) : previous);
                }}
            />

            <ConfirmationModal
                returnFocusFallback={searchInput}
                isOpen={Boolean(deleteTarget) && deleteQuery === query}
                error={actionError}
                title="Permanently delete feedback note?"
                message="This removes the reviewer audio and transcript permanently. This action cannot be undone."
                confirmLabel="Delete feedback"
                variant="danger"
                loading={deleting}
                reason={deleteReason}
                reasonReadOnly={reasonReadOnly}
                onReasonChange={value => { if (submittedReason.current === null) setDeleteReason(value); }}
                reasonLabel="Deletion reason"
                onConfirm={() => void deleteFeedback()}
                onCancel={closeDelete}
            />
        </div>
    );
}
