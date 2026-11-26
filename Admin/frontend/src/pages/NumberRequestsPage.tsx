import { useCallback } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FileCheck, RefreshCw } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { NumberRequestsTable } from '../components/NumberRequestsTable';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';
import {
    NUMBER_REQUEST_STATUSES, NUMBER_REQUEST_STATUS_LABELS, validNumberRequestPage, type NumberRequestPage,
} from '../lib/number-request-contracts';
import {
    canGoNext, nextOffset, numberRequestQuery, pageNumber, parseNumberRequestFilters, previousOffset,
    serializeNumberRequestFilters, withStatus, type NumberRequestFilters,
} from '../lib/number-request-filters';
import '../components/NumberRequests.css';

export function NumberRequestsPage() {
    const generation = useAuthGeneration();
    const [searchParams, setSearchParams] = useSearchParams();
    const filters = parseNumberRequestFilters(searchParams);
    const query = numberRequestQuery(filters);
    const href = (next: NumberRequestFilters) => {
        const search = serializeNumberRequestFilters(next);
        return search ? `/number-requests?${search}` : '/number-requests';
    };

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                {/* A new sign-in or a new status/position starts a new read: rows of another list are never shown as this one. */}
                <NumberRequestsContent key={`${generation}:${query}`} filters={filters} query={query} href={href}
                    goTo={(offset) => setSearchParams(serializeNumberRequestFilters({ ...filters, offset }))} />
            </main>
        </div>
    );
}

function NumberRequestsContent({ filters, query, href, goTo }: {
    filters: NumberRequestFilters; query: string; href: (next: NumberRequestFilters) => string; goTo: (offset: number) => void;
}) {
    const load = useCallback(() => api.getNumberRequests(query), [query]);
    // Every list read writes an audit row on the server: one read, then only on Refresh.
    const { data, error, loading, refresh } = useAdminRead<NumberRequestPage>(load, null, validNumberRequestPage);

    return (
        <div className="dashboard-content number-requests">
            <div className="page-header">
                <div className="page-header-icon"><FileCheck /></div>
                <div className="number-requests-heading">
                    <h1 className="page-title">Phone Number Requests</h1>
                    <p className="page-description">Phone number requests across every company. Open a request to download its documents, then approve or reject it.</p>
                </div>
                <div className="page-header-actions">
                    <button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void refresh()}>
                        <RefreshCw size={16} /> {error ? 'Retry' : 'Refresh'}
                    </button>
                </div>
            </div>

            <nav className="page-tabs number-request-tabs" aria-label="Filter by status">
                {NUMBER_REQUEST_STATUSES.map((status) => (
                    <Link key={status} to={href(withStatus(filters, status))}
                        className={`page-tab ${filters.status === status ? 'active' : ''}`}
                        aria-current={filters.status === status ? 'true' : undefined}>
                        {NUMBER_REQUEST_STATUS_LABELS[status]}
                    </Link>
                ))}
            </nav>

            {error && <div className="error-banner number-request-banner" role="alert"><p>Requests unavailable: {error}{data ? ' Showing the last successful read, which may be stale.' : ''}</p></div>}
            {loading && !data && <p role="status">Loading requests…</p>}
            {data && data.items.length === 0 && <p>No requests with this status.</p>}
            {data && data.items.length > 0 && (
                <div className="card">
                    <NumberRequestsTable items={data.items} />
                </div>
            )}
            {data && (
                <div className="number-request-pager">
                    <button type="button" className="btn btn-secondary" disabled={filters.offset === 0 || loading} onClick={() => goTo(previousOffset(filters))}>Previous page</button>
                    <span>Page {pageNumber(filters)}</span>
                    <button type="button" className="btn btn-secondary" disabled={!canGoNext(filters, data.has_more) || loading} onClick={() => goTo(nextOffset(filters))}>Next page</button>
                </div>
            )}
        </div>
    );
}
