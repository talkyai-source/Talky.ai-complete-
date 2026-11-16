import { useState, useEffect, useCallback, useRef } from 'react';
import {
    Filter,
    ChevronLeft,
    ChevronRight,
    Building2,
    RotateCcw,
} from 'lucide-react';
import { api } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { TenantFilter } from './TenantFilter';
import { useOperationOwner } from '../lib/use-operation-owner';
import type { AdminSipTrunkItem, AdminSipTrunkListParams } from '../lib/api';

/** The registration states the line-status updater stores (scripts/trunk_live_status_updater.py), in plain words. */
const REGISTRATION_STATES: ReadonlyArray<readonly [string, string]> = [
    ['registered', 'Registered'],
    ['reachable', 'Reachable'],
    ['loaded', 'Loaded, not confirmed'],
    ['checking', 'Checking'],
    ['unregistered', 'Not registered'],
    ['rejected', 'Rejected'],
    ['unreachable', 'Unreachable'],
    ['missing_config', 'Not set up'],
    ['unknown', 'Not known'],
    ['inactive', 'Switched off'],
];

const NOT_AVAILABLE = 'Not available';

// Same status styling the Calls and Campaigns tables use: green healthy, amber unsure, red a problem.
const HEALTHY = ['registered', 'reachable'];
const PROBLEM = ['rejected', 'unregistered', 'unreachable', 'missing_config'];
const UNSURE = ['loaded', 'checking', 'unknown'];

function registrationClass(status: string | null): string {
    if (status && HEALTHY.includes(status)) return 'status-completed';
    if (status && PROBLEM.includes(status)) return 'status-failed';
    if (status && UNSURE.includes(status)) return 'status-pending';
    return 'status-default';
}

function registrationLabel(status: string | null): string {
    if (!status) return NOT_AVAILABLE;
    return REGISTRATION_STATES.find(([value]) => value === status)?.[1] ?? status;
}

// Same date format as the Campaigns table; an unknown date is said, not left blank.
function formatDate(dateStr: string | null): string {
    if (!dateStr) return NOT_AVAILABLE;
    try {
        const date = new Date(dateStr);
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return dateStr;
    }
}

export function TrunksTable() {
    const generation = useAuthGeneration();
    return <OwnedTrunksTable key={generation} generation={generation} />;
}

/** Read-only list of every account's phone lines: GET /admin/sip-trunks, newest first (the server's order). */
function OwnedTrunksTable({ generation }: { generation: number }) {
    const [trunks, setTrunks] = useState<AdminSipTrunkItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [page, setPage] = useState(1);
    const [pageSize] = useState(20);
    const [total, setTotal] = useState(0);
    const [activeFilter, setActiveFilter] = useState('');
    const [registrationFilter, setRegistrationFilter] = useState('');
    const [tenantFilter, setTenantFilter] = useState('');

    const query = JSON.stringify([page, activeFilter, registrationFilter, tenantFilter]);
    const capture = useOperationOwner(generation, query);
    const request = useRef(0);
    const [loadedQuery, setLoadedQuery] = useState('');

    const fetchTrunks = useCallback(async () => {
        const owns = capture();
        if (!owns()) return;
        const sequence = ++request.current;
        const current = () => owns() && sequence === request.current;
        setLoading(true);
        try {
            const params: AdminSipTrunkListParams = {
                page,
                page_size: pageSize,
            };
            if (activeFilter === 'true' || activeFilter === 'false') params.is_active = activeFilter === 'true';
            if (REGISTRATION_STATES.some(([value]) => value === registrationFilter)) params.registration = registrationFilter;
            if (tenantFilter) params.tenant_id = tenantFilter;

            const response = await api.getAdminSipTrunks(params);
            if (!current()) return;
            setLoadedQuery(query);
            if (!response.error && (!response.data || !Array.isArray(response.data.items) || !Number.isSafeInteger(response.data.total))) throw new Error('The list response is incomplete.');
            if (response.error) throw new Error(response.error.message);
            if (response.data) {
                setTrunks(response.data.items);
                setTotal(response.data.total);
            }
            setError(null);
        } catch (err) {
            if (!current()) return;
            setLoadedQuery(query);
            setError(err instanceof Error ? err.message : 'Failed to load phone lines');
        } finally {
            if (current()) setLoading(false);
        }
    }, [page, pageSize, activeFilter, registrationFilter, tenantFilter, capture, query]);

    useEffect(() => {
        fetchTrunks();
    }, [fetchTrunks]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="call-history-container">
            {/* Toolbar */}
            <div className="table-toolbar">
                <div className="filter-group">
                    <Filter size={16} />
                    <select
                        value={activeFilter}
                        onChange={(e) => {
                            setActiveFilter(e.target.value);
                            setPage(1);
                        }}
                        className="filter-select"
                        aria-label="Switched on"
                    >
                        <option value="">All</option>
                        <option value="true">Yes</option>
                        <option value="false">No</option>
                    </select>
                </div>
                <select
                    value={registrationFilter}
                    onChange={(event) => {
                        setRegistrationFilter(event.target.value);
                        setPage(1);
                    }}
                    className="filter-select"
                    aria-label="Registration"
                >
                    <option value="">All registrations</option>
                    {REGISTRATION_STATES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <TenantFilter includeArchived value={tenantFilter} onChange={value => { setTenantFilter(value); setPage(1); }} />
                {(activeFilter || registrationFilter || tenantFilter) && (
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                            setActiveFilter('');
                            setRegistrationFilter('');
                            setTenantFilter('');
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
                        <p>Loading phone lines…</p>
                    </div>
                ) : error ? (
                    <div className="error-banner">
                        <p>{error}</p>
                        <button onClick={fetchTrunks}>Retry</button>
                    </div>
                ) : trunks.length === 0 ? (
                    <div className="empty-state">
                        <p>No phone lines found. Try adjusting the filters.</p>
                    </div>
                ) : (
                    <table className="data-table campaigns-table">
                        <thead>
                            <tr>
                                <th>Line</th>
                                <th>Account</th>
                                <th>Address</th>
                                <th>Direction</th>
                                <th>Role</th>
                                <th>Switched on</th>
                                <th>Sign-in details saved</th>
                                <th>Registration</th>
                                <th>Detail</th>
                                <th>Last checked</th>
                                <th>Last tested</th>
                                <th>Created</th>
                            </tr>
                        </thead>
                        <tbody>
                            {trunks.map((trunk) => (
                                <tr key={trunk.id}>
                                    <td className="campaigns-table-wrap">{trunk.trunk_name}</td>
                                    <td className="campaigns-table-wrap">
                                        <div className="tenant-name-cell">
                                            <Building2 size={14} />
                                            <span>{trunk.tenant_name ?? NOT_AVAILABLE}</span>
                                        </div>
                                    </td>
                                    <td className="campaigns-table-wrap">{`${trunk.sip_domain}:${trunk.port} (${trunk.transport})`}</td>
                                    <td>
                                        <span className={`direction-badge direction-${trunk.direction || 'unknown'}`}>{trunk.direction || 'unknown'}</span>
                                    </td>
                                    <td>{trunk.role || NOT_AVAILABLE}</td>
                                    <td>{trunk.is_active ? 'Yes' : 'No'}</td>
                                    <td>{trunk.auth_configured ? 'Yes' : 'No'}</td>
                                    <td>
                                        <span className={`call-status-badge ${registrationClass(trunk.live_registration_status)}`}>{registrationLabel(trunk.live_registration_status)}</span>
                                    </td>
                                    <td className="campaigns-table-wrap">{trunk.live_status_detail || NOT_AVAILABLE}</td>
                                    <td>{formatDate(trunk.live_status_checked_at)}</td>
                                    <td>{formatDate(trunk.last_tested_at)}</td>
                                    <td>{formatDate(trunk.created_at)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>

            {/* Pagination */}
            {!loading && loadedQuery === query && !error && trunks.length > 0 && (
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
