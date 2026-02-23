import { useState, useEffect, useCallback } from 'react';
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

    const fetchTenants = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await api.getTenants(
                searchTerm || undefined,
                statusFilter || undefined
            );
            setTenants(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch tenants');
            console.error('Failed to fetch tenants:', err);
        } finally {
            setLoading(false);
        }
    }, [searchTerm, statusFilter]);

    useEffect(() => {
        fetchTenants();
    }, [fetchTenants]);

    // Debounced search
    useEffect(() => {
        const timeoutId = setTimeout(() => {
            fetchTenants();
        }, 300);
        return () => clearTimeout(timeoutId);
    }, [searchTerm, statusFilter, fetchTenants]);

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
