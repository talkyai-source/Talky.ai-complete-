import { useCallback, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { LifeBuoy, RefreshCw, Search } from 'lucide-react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { TenantFilter } from '../components/TenantFilter';
import { SupportQueueTable } from '../components/SupportQueueTable';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';
import {
    PRIORITY_LABELS, STATUS_LABELS, SUPPORT_CATEGORIES, SUPPORT_PRIORITIES, SUPPORT_STATUSES, validQueuePage, type StaffQueuePage,
} from '../lib/support-contracts';
import {
    parseQueueFilters, queueQueryString, serializeQueueFilters, withFilterChange, type QueueFilters,
} from '../lib/support-queue-filters';
import '../components/SupportConsole.css';

type QueuePageRead = StaffQueuePage & { fetched_at: number };

/** The read carries its own clock so deadline colouring never reads Date.now() during render. */
async function loadQueuePage(query: string): Promise<{ data?: QueuePageRead; error?: { message: string } }> {
    const response = await api.getSupportTickets(query);
    return response.data ? { data: { ...response.data, fetched_at: Date.now() } } : { error: response.error ?? { message: 'Tickets unavailable.' } };
}

export function SupportTicketsPage() {
    const generation = useAuthGeneration();
    return <SupportTicketsContent key={generation} />;
}

function SupportTicketsContent() {
    const [searchParams, setSearchParams] = useSearchParams();
    const filters = parseQueueFilters(searchParams);
    // A primitive copy: the read's key must not alias the filters object the handlers derive new filters from.
    const query = `${queueQueryString(filters)}`;
    const [draftQuery, setDraftQuery] = useState(filters.q);
    const [sortByDeadline, setSortByDeadline] = useState(true);
    // Keyset cursors only move forward; remember the cursors we came from.
    const [trail, setTrail] = useState<{ key: string; cursors: string[] }>({ key: '', cursors: [] });

    const load = useCallback(() => loadQueuePage(query), [query]);
    const { data, error, loading, refresh } = useAdminRead(load, 30000, validQueuePage);

    const filterKey = serializeQueueFilters({ ...filters, cursor: '' });
    const cursors = trail.key === filterKey ? trail.cursors : [];

    const apply = (next: QueueFilters) => {
        setSearchParams(serializeQueueFilters(next));
    };
    const change = (patch: Partial<Omit<QueueFilters, 'cursor'>>) => {
        setTrail({ key: '', cursors: [] });
        apply(withFilterChange(filters, patch));
    };
    const nextPage = () => {
        if (!data?.next_cursor) return;
        setTrail({ key: filterKey, cursors: [...cursors, filters.cursor] });
        apply({ ...filters, cursor: data.next_cursor });
    };
    const previousPage = () => {
        const previous = cursors[cursors.length - 1];
        if (previous === undefined) return;
        setTrail({ key: filterKey, cursors: cursors.slice(0, -1) });
        apply({ ...filters, cursor: previous });
    };

    return (
        <div className="app-layout">
            <Sidebar />
            <main className="main-content">
                <Header />
                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon"><LifeBuoy /></div>
                        <div>
                            <h1 className="page-title">Support Tickets</h1>
                            <p className="page-description">Customer tickets across every company. Open a ticket to reply, change its status or assign it.</p>
                        </div>
                        <div className="page-header-actions">
                            <Link className="btn btn-secondary" to="/support/articles">Help articles</Link>
                            <button type="button" className="btn btn-secondary" disabled={loading} onClick={() => void refresh()}>
                                <RefreshCw size={16} /> Refresh
                            </button>
                        </div>
                    </div>

                    <form className="support-toolbar" role="search" onSubmit={(event) => { event.preventDefault(); change({ q: draftQuery.trim().slice(0, 100) }); }}>
                        <label>Status
                            <select className="filter-select" aria-label="Filter by status" value={filters.status} onChange={(event) => change({ status: event.target.value })}>
                                <option value="">Any status</option>
                                {SUPPORT_STATUSES.map((status) => <option key={status} value={status}>{STATUS_LABELS[status]}</option>)}
                            </select>
                        </label>
                        <label>Priority
                            <select className="filter-select" aria-label="Filter by priority" value={filters.priority} onChange={(event) => change({ priority: event.target.value })}>
                                <option value="">Any priority</option>
                                {SUPPORT_PRIORITIES.map((priority) => <option key={priority} value={priority}>{PRIORITY_LABELS[priority]}</option>)}
                            </select>
                        </label>
                        <label>Category
                            <select className="filter-select" aria-label="Filter by category" value={filters.category} onChange={(event) => change({ category: event.target.value })}>
                                <option value="">Any category</option>
                                {SUPPORT_CATEGORIES.map((category) => <option key={category.id} value={category.id}>{category.label}</option>)}
                            </select>
                        </label>
                        <label>Assignee
                            <select className="filter-select" aria-label="Filter by assignee" value={filters.assignee} onChange={(event) => change({ assignee: event.target.value as QueueFilters['assignee'] })}>
                                <option value="">Anyone</option>
                                <option value="me">Me</option>
                                <option value="unassigned">Unassigned</option>
                            </select>
                        </label>
                        <label>Deadline
                            <select className="filter-select" aria-label="Filter by deadline" value={filters.sla} onChange={(event) => change({ sla: event.target.value as QueueFilters['sla'] })}>
                                <option value="">Any</option>
                                <option value="breached">Breached</option>
                                <option value="at_risk">At risk</option>
                            </select>
                        </label>
                        <label>Company
                            <TenantFilter value={filters.tenant_id} onChange={(value) => change({ tenant_id: value })} label="Filter by company" emptyLabel="All companies" />
                        </label>
                        <label>Search
                            <span className="search-box">
                                <Search size={16} />
                                <input className="search-input" aria-label="Search by ticket number or subject" placeholder="SUP-000123 or subject" value={draftQuery} onChange={(event) => setDraftQuery(event.target.value)} />
                            </span>
                        </label>
                        <button type="submit" className="btn btn-secondary">Apply search</button>
                        <label style={{ flexDirection: 'row', alignItems: 'center' }}>
                            <input type="checkbox" checked={sortByDeadline} onChange={(event) => setSortByDeadline(event.target.checked)} />
                            Nearest deadline first (this page only)
                        </label>
                    </form>

                    <p className="support-note">
                        The server returns tickets by last update, 50 at a time. The deadline sort reorders only the page shown here, not the whole queue.
                        The requester is not provided by the list endpoint; open a ticket to see who raised it.
                    </p>

                    {error && <div className="error-banner" role="alert">Tickets unavailable: {error}{data ? ' Showing the last successful read, which may be stale.' : ''}</div>}
                    {loading && !data && <p role="status">Loading tickets…</p>}
                    {data && data.items.length === 0 && <p>No tickets match these filters.</p>}
                    {data && data.items.length > 0 && (
                        <div className="card">
                            <SupportQueueTable items={data.items} now={data.fetched_at} sortByDeadline={sortByDeadline} />
                        </div>
                    )}
                    {data && (
                        <div className="support-pager">
                            <button type="button" className="btn btn-secondary" disabled={cursors.length === 0 || loading} onClick={previousPage}>Previous page</button>
                            <span>Page {cursors.length + 1}</span>
                            <button type="button" className="btn btn-secondary" disabled={!data.next_cursor || loading} onClick={nextPage}>Next page</button>
                        </div>
                    )}
                </div>
            </main>
        </div>
    );
}
