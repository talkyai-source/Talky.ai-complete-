import { useState, useEffect, useCallback, useRef } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { TenantsTable } from '../components/TenantsTable';
import { FormalHoldPanel } from '../components/FormalHoldPanel';
import { Building2, RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import type { TenantListItem } from '../lib/api';
import { useAuthGeneration } from '../lib/use-auth-generation';

export function TenantsPage() {
    const generation = useAuthGeneration();
    return <OwnedTenantsPage key={generation} generation={generation} />;
}

function OwnedTenantsPage({ generation }: { generation: number }) {
    const formalReturnFocus = useRef<HTMLInputElement>(null);
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [page, setPage] = useState(0);
    const [hasMore, setHasMore] = useState(false);
    const [formalTenant, setFormalTenant] = useState<TenantListItem | null>(null);
    const mounted = useRef(false);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; requestSeq.current += 1; }; }, []);
    // What is typed in the box (searchTerm) is applied to the query only after a
    // pause, so one request fires per search instead of one per keystroke.
    const [debouncedSearch, setDebouncedSearch] = useState('');
    // Only the newest request may publish: an earlier, slower reply must not
    // overwrite the results for the term now in the box.
    const requestSeq = useRef(0);

    const fetchTenants = useCallback(async () => {
        const seq = ++requestSeq.current;
        const owns = () => mounted.current && seq === requestSeq.current && generation === api.getAuthGeneration();
        if (!owns()) return;
        setLoading(true);
        setError(null);
        try {
            // api.getTenants returns the ApiResponse wrapper { data, error } —
            // unwrap it (every other page does this). The endpoint returns a
            // bare array, so default to [] to keep TenantsTable iterable.
            const res = await api.getTenants(
                debouncedSearch || undefined,
                statusFilter || undefined, 51, page * 50
            );
            if (!owns()) return;
            if (res.error || !res.data) {
                setError(res.error?.message ?? 'The server returned no tenant records.');
                setTenants([]);
                setHasMore(false);
            } else {
                setTenants(res.data.slice(0, 50));
                setHasMore(res.data.length > 50);
            }
        } catch (err) {
            if (!owns()) return;
            setError(err instanceof Error ? err.message : 'Failed to fetch tenants');
            setTenants([]); setHasMore(false);
        } finally {
            if (owns()) setLoading(false);
        }
    }, [debouncedSearch, statusFilter, page, generation]);

    // Immediate on mount and on a filter change; a search term arrives debounced.
    useEffect(() => {
        void fetchTenants();
    }, [fetchTenants]);

    useEffect(() => {
        if (searchTerm === debouncedSearch) return;
        const timeoutId = setTimeout(() => { setDebouncedSearch(searchTerm); setPage(0); }, 300);
        return () => clearTimeout(timeoutId);
    }, [searchTerm, debouncedSearch]);

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <Building2 />
                        </div>
                        <div>
                            <h1 className="page-title">Tenants</h1>
                            <p className="page-description">
                                Manage all tenants, subscriptions, and quotas
                            </p>
                        </div>
                        <div className="page-header-actions">
                            <button
                                className="btn btn-secondary"
                                onClick={fetchTenants}
                                disabled={loading}
                            >
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} />
                                Refresh
                            </button>
                        </div>
                    </div>

                    {error && (
                        <div className="error-banner" role="alert">
                            <p>{error}</p>
                            <button onClick={fetchTenants}>Retry</button>
                        </div>
                    )}

                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">All Tenants</h3>
                            <span className="card-count">{tenants.length} shown · Page {page + 1}</span>
                        </div>
                        <div className="card-body">
                            <TenantsTable
                                tenants={tenants}
                                loading={loading}
                                readError={error}
                                onRefresh={fetchTenants}
                                onFormalHolds={setFormalTenant}
                                searchInputRef={formalReturnFocus}
                                searchTerm={searchTerm}
                                onSearchChange={setSearchTerm}
                                statusFilter={statusFilter}
                                onStatusFilterChange={(status) => { setStatusFilter(status); setPage(0); }}
                            />
                            <div className="pagination">
                                <button disabled={loading || page === 0} onClick={() => setPage((v) => v - 1)}>Previous</button>
                                <button disabled={loading || !hasMore} onClick={() => setPage((v) => v + 1)}>Next</button>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
            {formalTenant && <FormalHoldPanel target={{ type: 'tenant', id: formalTenant.id, label: formalTenant.business_name }}
                returnFocusFallback={formalReturnFocus} onClose={() => setFormalTenant(null)} onChanged={() => { void fetchTenants(); }} />}
        </div>
    );
}
