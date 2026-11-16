import { useState, useEffect, useCallback, useRef } from 'react';
import {
    Search,
    Filter,
    ChevronLeft,
    ChevronRight,
    Building2,
    RotateCcw,
    PhoneIncoming,
    PhoneOutgoing,
} from 'lucide-react';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { TenantFilter } from './TenantFilter';
import { useOperationOwner } from '../lib/use-operation-owner';
import type { AdminCampaignItem, AdminCampaignListParams } from '../lib/api';

/** The stored campaign statuses (backend domain/models/campaign.py), shown as the server sends them. */
export const CAMPAIGN_STATUSES = ['draft', 'scheduled', 'running', 'paused', 'completed', 'cancelled', 'stopped', 'deleted'] as const;

/** The server refuses a search longer than this (admin/campaigns.py: max_length=120). */
export const CAMPAIGN_SEARCH_MAX = 120;

// Same date format as the Calls history table.
function formatDate(dateStr: string | null): string {
    if (!dateStr) return '-';
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return dateStr;
    }
}

export function CampaignsTable() {
    const generation = useAuthGeneration();
    return <OwnedCampaignsTable key={generation} generation={generation} />;
}

/** Read-only list of every tenant's campaigns: GET /admin/campaigns, newest first (the server's order). */
function OwnedCampaignsTable({ generation }: { generation: number }) {
    const [campaigns, setCampaigns] = useState<AdminCampaignItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [tenantFilter, setTenantFilter] = useState('');
    const [directionFilter, setDirectionFilter] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');

    const query = JSON.stringify([page, debouncedSearch, statusFilter, tenantFilter, directionFilter]);
    const capture = useOperationOwner(generation, query);
    const request = useRef(0);
    const [loadedQuery, setLoadedQuery] = useState('');

    // Debounce search. The page returns to 1 only when the search really
    // changed, so the timer that follows the first render moves nothing.
    useEffect(() => {
        const next = search.trim();
        if (next === debouncedSearch) return;
        const timer = setTimeout(() => {
            setDebouncedSearch(next);
            setPage(1);
        }, 300);
        return () => clearTimeout(timer);
    }, [search, debouncedSearch]);

    const fetchCampaigns = useCallback(async () => {
        const owns = capture();
        if (!owns()) return;
        const sequence = ++request.current;
        const current = () => owns() && sequence === request.current;
        setLoading(true);
        try {
            const params: AdminCampaignListParams = {
                page,
                page_size: pageSize,
            };
            // The server accepts a search of 1 to 120 characters; anything else is not sent.
            if (debouncedSearch.length >= 1 && debouncedSearch.length <= CAMPAIGN_SEARCH_MAX) params.search = debouncedSearch;
            if (statusFilter) params.status = statusFilter;
            if (tenantFilter) params.tenant_id = tenantFilter;
            if (directionFilter === 'inbound' || directionFilter === 'outbound') {
                params.direction = directionFilter;
            }

            const response = await api.getAdminCampaigns(params);
            if (!current()) return;
            setLoadedQuery(query);
            if (!response.error && (!response.data || !Array.isArray(response.data.items) || !Number.isSafeInteger(response.data.total))) throw new Error('The list response is incomplete.');
            if (response.error) throw new Error(response.error.message);
            if (response.data) {
                setCampaigns(response.data.items);
                setTotal(response.data.total);
            }
            setError(null);
        } catch (err) {
            if (!current()) return;
            setLoadedQuery(query);
            setError(err instanceof Error ? err.message : 'Failed to load campaigns');
        } finally {
            if (current()) setLoading(false);
        }
    }, [page, pageSize, debouncedSearch, statusFilter, tenantFilter, directionFilter, capture, query]);

    useEffect(() => {
        fetchCampaigns();
    }, [fetchCampaigns]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="call-history-container">
            {/* Toolbar */}
            <div className="table-toolbar">
                <div className="search-box">
                    <Search size={18} />
                    <input
                        type="text"
                        placeholder="Search campaigns…"
                        aria-label="Search campaigns…"
                        maxLength={CAMPAIGN_SEARCH_MAX}
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
                        aria-label="Filter by status"
                    >
                        <option value="">All Statuses</option>
                        {CAMPAIGN_STATUSES.map((status) => <option key={status} value={status}>{status}</option>)}
                    </select>
                </div>
                <select
                    value={directionFilter}
                    onChange={(event) => {
                        setDirectionFilter(event.target.value);
                        setPage(1);
                    }}
                    className="filter-select"
                    aria-label="Direction"
                >
                    <option value="">All directions</option>
                    <option value="outbound">Outbound</option>
                    <option value="inbound">Inbound</option>
                </select>
                <TenantFilter includeArchived value={tenantFilter} onChange={value => { setTenantFilter(value); setPage(1); }} />
                {(statusFilter || tenantFilter || directionFilter || search) && (
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                            setSearch('');
                            setStatusFilter('');
                            setTenantFilter('');
                            setDirectionFilter('');
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
                        <p>Loading campaigns…</p>
                    </div>
                ) : error ? (
                    <div className="error-banner">
                        <p>{error}</p>
                        <button onClick={fetchCampaigns}>Retry</button>
                    </div>
                ) : campaigns.length === 0 ? (
                    <div className="empty-state">
                        <p>Try adjusting your search or filter criteria.</p>
                    </div>
                ) : (
                    <table className="data-table campaigns-table">
                        <thead>
                            <tr>
                                <th>Campaign</th>
                                <th>Tenant</th>
                                <th>Status</th>
                                <th>Direction</th>
                                <th>Contacts</th>
                                <th>Calls</th>
                                <th>Live Calls</th>
                                <th>Created</th>
                                <th>Started</th>
                            </tr>
                        </thead>
                        <tbody>
                            {campaigns.map((campaign) => (
                                <tr key={campaign.id}>
                                    <td className="campaigns-table-wrap">{campaign.name}</td>
                                    <td className="campaigns-table-wrap">
                                        <div className="tenant-name-cell">
                                            <Building2 size={14} />
                                            <span>{campaign.tenant_name}</span>
                                        </div>
                                    </td>
                                    <td>
                                        <span className="call-status-badge status-default">{campaign.status}</span>
                                    </td>
                                    <td>
                                        <span className={`direction-badge direction-${campaign.direction || 'unknown'}`}>
                                            {campaign.direction === 'inbound' ? <PhoneIncoming size={13} /> : campaign.direction === 'outbound' ? <PhoneOutgoing size={13} /> : null}
                                            {campaign.direction || 'unknown'}
                                        </span>
                                    </td>
                                    <td>{campaign.contacts}</td>
                                    <td>{campaign.calls}</td>
                                    <td>{campaign.live_calls}</td>
                                    <td>{formatDate(campaign.created_at)}</td>
                                    <td>{formatDate(campaign.started_at)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Pagination */}
            {!loading && loadedQuery === query && !error && campaigns.length > 0 && (
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
