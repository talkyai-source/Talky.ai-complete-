import { useCallback, useEffect, useState } from 'react';
import {
    AudioLines,
    Building2,
    CalendarDays,
    ChevronLeft,
    ChevronRight,
    ExternalLink,
    Filter,
    HardDrive,
    RotateCcw,
    Search,
    ShieldAlert,
    Trash2,
} from 'lucide-react';

import { api } from '../lib/api';
import type { AdminRecordingItem, AdminRecordingParams, TenantListItem } from '../lib/api';
import { AdminMediaPlayer } from './AdminMediaPlayer';
import { ConfirmationModal } from './ConfirmationModal';

interface RecordingsTableProps {
    onCallSelect: (callId: string) => void;
}

function formatBytes(value: number | null): string {
    if (value === null || value < 0) return '—';
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(value: number | null): string {
    if (value === null) return '—';
    const minutes = Math.floor(value / 60);
    const seconds = Math.max(0, value % 60);
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function formatDate(value: string): string {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function RecordingsTable({ onCallSelect }: RecordingsTableProps) {
    const [items, setItems] = useState<AdminRecordingItem[]>([]);
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
    const [direction, setDirection] = useState('');
    const [fromDate, setFromDate] = useState('');
    const [toDate, setToDate] = useState('');
    const [deleteTarget, setDeleteTarget] = useState<AdminRecordingItem | null>(null);
    const [deleteReason, setDeleteReason] = useState('');
    const [deleteIdempotencyKey, setDeleteIdempotencyKey] = useState('');
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
        const params: AdminRecordingParams = { page, page_size: pageSize };
        if (debouncedSearch) params.search = debouncedSearch;
        if (status) params.status = status;
        if (tenantId) params.tenant_id = tenantId;
        if (direction === 'inbound' || direction === 'outbound') params.direction = direction;
        if (fromDate) params.from_date = fromDate;
        if (toDate) params.to_date = toDate;
        const response = await api.getAdminRecordings(params);
        if (response.error) {
            setError(response.error.message);
            setItems([]);
        } else if (response.data) {
            setItems(response.data.items);
            setTotal(response.data.total);
        }
        setLoading(false);
    }, [page, debouncedSearch, status, tenantId, direction, fromDate, toDate]);

    useEffect(() => {
        // The request is intentionally tied to the complete filter snapshot.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        void fetchItems();
    }, [fetchItems]);

    const deleteRecording = async () => {
        if (!deleteTarget || !deleteIdempotencyKey) return;
        setDeleting(true);
        const response = await api.deleteAdminRecording(
            deleteTarget.id,
            deleteReason.trim(),
            deleteIdempotencyKey,
        );
        if (response.error) {
            setError(response.error.message);
        } else {
            setDeleteTarget(null);
            setDeleteReason('');
            setDeleteIdempotencyKey('');
            await fetchItems();
        }
        setDeleting(false);
    };

    const openDelete = (item: AdminRecordingItem) => {
        if (item.legal_hold) return;
        const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        setDeleteTarget(item);
        setDeleteReason('');
        setDeleteIdempotencyKey(`admin-media:recording:${item.id}:${id}`);
    };

    const closeDelete = () => {
        if (deleting) return;
        setDeleteTarget(null);
        setDeleteReason('');
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
            <div className="table-toolbar media-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        className="search-input"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder="Phone, call ID, tenant, or campaign…"
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
                        <option value="">All recording states</option>
                        <option value="uploaded">Available</option>
                        <option value="uploading">Uploading</option>
                        <option value="failed">Failed</option>
                        <option value="deleted">Deleted metadata</option>
                    </select>
                </div>
                <select
                    className="filter-select"
                    value={direction}
                    onChange={(event) => { setDirection(event.target.value); setPage(1); }}
                    aria-label="Filter recordings by direction"
                >
                    <option value="">All directions</option>
                    <option value="inbound">Inbound</option>
                    <option value="outbound">Outbound</option>
                </select>
                <select
                    className="filter-select"
                    value={tenantId}
                    onChange={(event) => {
                        setTenantId(event.target.value);
                        setPage(1);
                    }}
                    aria-label="Filter recordings by tenant"
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
                        aria-label="Recordings from date"
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
                        aria-label="Recordings to date"
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
                        <p>Loading recordings…</p>
                    </div>
                ) : items.length === 0 ? (
                    <div className="empty-state">
                        <AudioLines size={48} />
                        <h3>No recordings found</h3>
                        <p>Try a different tenant, date range, or recording state.</p>
                    </div>
                ) : (
                    <table className="data-table admin-media-table">
                        <thead>
                            <tr>
                                <th>Created</th>
                                <th>Tenant / Call</th>
                                <th>Campaign</th>
                                <th>Audio</th>
                                <th>Size</th>
                                <th>Status</th>
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
                                            <span className={`direction-badge direction-${item.direction || 'outbound'}`}>
                                                {item.direction || 'outbound'}{item.called_did ? ` · DID ${item.called_did}` : ''}
                                            </span>
                                            <button className="link-button" onClick={() => onCallSelect(item.call_id)}>
                                                {item.call_id.slice(0, 8)}… <ExternalLink size={11} />
                                            </button>
                                        </div>
                                    </td>
                                    <td>{item.campaign_name || '—'}</td>
                                    <td>
                                        <AdminMediaPlayer
                                            compact
                                            disabled={!item.playable}
                                            filename={`call-${item.call_id}.wav`}
                                            load={() => api.getAdminRecordingAudio(item.id)}
                                        />
                                        <span className="media-duration">{formatDuration(item.duration_seconds)}</span>
                                    </td>
                                    <td>
                                        <span className="storage-label"><HardDrive size={13} /> {formatBytes(item.file_size_bytes)}</span>
                                        <span className="storage-kind">{item.storage}</span>
                                    </td>
                                    <td>
                                        <span className={`call-status-badge status-${item.status}`}>{item.status}</span>
                                    </td>
                                    <td>
                                        <div className="row-actions">
                                            <button className="btn btn-secondary btn-sm" onClick={() => onCallSelect(item.call_id)}>
                                                <ExternalLink size={14} /> Call
                                            </button>
                                            <button
                                                className="btn btn-danger btn-sm"
                                                onClick={() => openDelete(item)}
                                                disabled={item.legal_hold}
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
                title="Permanently delete recording?"
                message={`This permanently removes the audio for ${deleteTarget?.phone_number || 'this call'} from storage and cannot be undone.`}
                confirmLabel="Delete recording"
                variant="danger"
                loading={deleting}
                reason={deleteReason}
                onReasonChange={setDeleteReason}
                reasonLabel="Deletion reason"
                onConfirm={() => void deleteRecording()}
                onCancel={closeDelete}
            />
        </div>
    );
}
