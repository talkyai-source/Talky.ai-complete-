import { useState, useEffect, useCallback, useRef } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { TenantsTable } from '../components/TenantsTable';
import { Building2, RefreshCw } from 'lucide-react';
import { api } from '../lib/api';
import type { TenantListItem } from '../lib/api';

export function TenantsPage() {
    const [tenants, setTenants] = useState<TenantListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    // What is typed in the box (searchTerm) is applied to the query only after a
    // pause, so one request fires per search instead of one per keystroke.
    const [debouncedSearch, setDebouncedSearch] = useState('');
    // Only the newest request may publish: an earlier, slower reply must not
    // overwrite the results for the term now in the box.
    const requestSeq = useRef(0);

    const fetchTenants = useCallback(async () => {
        const seq = ++requestSeq.current;
        setLoading(true);
        setError(null);
        try {
            // api.getTenants returns the ApiResponse wrapper { data, error } —
            // unwrap it (every other page does this). The endpoint returns a
            // bare array, so default to [] to keep TenantsTable iterable.
            const res = await api.getTenants(
                debouncedSearch || undefined,
                statusFilter || undefined
            );
            if (seq !== requestSeq.current) return;
            if (res.error) {
                setError(res.error.message);
                setTenants([]);
            } else {
                setTenants(res.data ?? []);
            }
        } catch (err) {
            if (seq !== requestSeq.current) return;
            setError(err instanceof Error ? err.message : 'Failed to fetch tenants');
            console.error('Failed to fetch tenants:', err);
        } finally {
            if (seq === requestSeq.current) setLoading(false);
        }
    }, [debouncedSearch, statusFilter]);

    // Immediate on mount and on a filter change; a search term arrives debounced.
    useEffect(() => {
        void fetchTenants();
    }, [fetchTenants]);

    useEffect(() => {
        const timeoutId = setTimeout(() => setDebouncedSearch(searchTerm), 300);
        return () => clearTimeout(timeoutId);
    }, [searchTerm]);

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
                        <div className="error-banner">
                            <p>{error}</p>
                            <button onClick={fetchTenants}>Retry</button>
                        </div>
                    )}

                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">All Tenants</h3>
                            <span className="card-count">{tenants.length} total</span>
                        </div>
                        <div className="card-body">
                            <TenantsTable
                                tenants={tenants}
                                loading={loading}
                                onRefresh={fetchTenants}
                                searchTerm={searchTerm}
                                onSearchChange={setSearchTerm}
                                statusFilter={statusFilter}
                                onStatusFilterChange={setStatusFilter}
                            />
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
