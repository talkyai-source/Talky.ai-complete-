import { useState, useEffect, useCallback } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { UsageBreakdownCard } from '../components/UsageBreakdownCard';
import { DollarSign, RefreshCw, Info, Building2 } from 'lucide-react';
import { api } from '../lib/api';

// Shape returned by GET /admin/usage/breakdown?group_by=tenant
interface TenantUsageRow {
    tenant_id: string;
    tenant_name: string;
    call_count: number;
    total_minutes: number;
    total_cost: number;
}

// First day of the current month, YYYY-MM-DD (UTC, matching the backend).
function monthStart(): string {
    const now = new Date();
    return `${now.getUTCFullYear()}-${String(now.getUTCMonth() + 1).padStart(2, '0')}-01`;
}
function today(): string {
    return new Date().toISOString().slice(0, 10);
}

export function UsageCostPage() {
    const [fromDate, setFromDate] = useState(monthStart());
    const [toDate, setToDate] = useState(today());
    const [tenantRows, setTenantRows] = useState<TenantUsageRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    // Bumped on Refresh / date change to force the summary card to refetch.
    const [reloadKey, setReloadKey] = useState(0);

    const fetchTenantBreakdown = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.getUsageBreakdown({
                group_by: 'tenant',
                from_date: fromDate,
                to_date: toDate,
            });
            if (res.error) {
                setError(res.error.message);
                setTenantRows([]);
            } else {
                const rows = (res.data?.breakdown ?? []) as unknown as TenantUsageRow[];
                // Highest spend / usage first.
                rows.sort((a, b) => (b.total_minutes ?? 0) - (a.total_minutes ?? 0));
                setTenantRows(rows);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load usage breakdown');
            setTenantRows([]);
        } finally {
            setLoading(false);
        }
    }, [fromDate, toDate]);

    useEffect(() => {
        fetchTenantBreakdown();
    }, [fetchTenantBreakdown, reloadKey]);

    const refresh = () => setReloadKey((k) => k + 1);

    const tenantTotalMinutes = tenantRows.reduce((s, r) => s + (r.total_minutes ?? 0), 0);
    const tenantTotalCalls = tenantRows.reduce((s, r) => s + (r.call_count ?? 0), 0);

    return (
        <div className="app-layout">
            <Sidebar />

            <main className="main-content">
                <Header />

                <div className="dashboard-content">
                    <div className="page-header">
                        <div className="page-header-icon">
                            <DollarSign />
                        </div>
                        <div>
                            <h1 className="page-title">Usage &amp; Cost</h1>
                            <p className="page-description">Monitor platform usage and billing analytics</p>
                        </div>
                        <div className="page-header-actions">
                            <div className="date-range-filter">
                                <label>
                                    From
                                    <input
                                        type="date"
                                        value={fromDate}
                                        max={toDate}
                                        onChange={(e) => setFromDate(e.target.value)}
                                    />
                                </label>
                                <label>
                                    To
                                    <input
                                        type="date"
                                        value={toDate}
                                        min={fromDate}
                                        max={today()}
                                        onChange={(e) => setToDate(e.target.value)}
                                    />
                                </label>
                            </div>
                            <button className="btn btn-secondary" onClick={refresh} disabled={loading}>
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} />
                                Refresh
                            </button>
                        </div>
                    </div>

                    <div className="usage-disclaimer">
                        <Info size={15} />
                        <span>
                            Call minutes and API-call counts are actual. Provider costs are estimates
                            derived from usage (Deepgram ~$0.0325/min, Groq ~$0.01/call), not billed
                            amounts. Per-tenant cost shows $0 until telephony cost is recorded on each call.
                        </span>
                    </div>

                    {error && (
                        <div className="error-banner">
                            <p>{error}</p>
                            <button onClick={refresh}>Retry</button>
                        </div>
                    )}

                    {/* Summary + provider breakdown (self-fetching, date-aware) */}
                    <UsageBreakdownCard
                        key={`${fromDate}|${toDate}|${reloadKey}`}
                        fromDate={fromDate}
                        toDate={toDate}
                    />

                    {/* Per-tenant breakdown */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">
                                <Building2 size={18} />
                                Usage by Tenant
                            </h3>
                            <span className="card-count">{tenantRows.length} tenants</span>
                        </div>
                        <div className="card-body">
                            <div className="table-container">
                                {loading ? (
                                    <div className="table-loading">
                                        <RefreshCw className="spinning" size={20} />
                                        <span>Loading tenant usage…</span>
                                    </div>
                                ) : tenantRows.length === 0 ? (
                                    <div className="empty-state">
                                        <DollarSign size={40} />
                                        <p>No usage recorded for this period.</p>
                                    </div>
                                ) : (
                                    <table className="data-table">
                                        <thead>
                                            <tr>
                                                <th>Tenant</th>
                                                <th style={{ textAlign: 'right' }}>Calls</th>
                                                <th style={{ textAlign: 'right' }}>Minutes</th>
                                                <th style={{ textAlign: 'right' }}>Cost</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {tenantRows.map((r) => (
                                                <tr key={r.tenant_id}>
                                                    <td>{r.tenant_name || 'Unknown'}</td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        {(r.call_count ?? 0).toLocaleString()}
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        {(r.total_minutes ?? 0).toLocaleString()}
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        ${(r.total_cost ?? 0).toFixed(2)}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                        <tfoot>
                                            <tr>
                                                <td><strong>Total</strong></td>
                                                <td style={{ textAlign: 'right' }}>
                                                    <strong>{tenantTotalCalls.toLocaleString()}</strong>
                                                </td>
                                                <td style={{ textAlign: 'right' }}>
                                                    <strong>{tenantTotalMinutes.toLocaleString()}</strong>
                                                </td>
                                                <td style={{ textAlign: 'right' }}>—</td>
                                            </tr>
                                        </tfoot>
                                    </table>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
}
