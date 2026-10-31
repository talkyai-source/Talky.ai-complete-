import { useState, useEffect, useCallback, useRef } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Header } from '../components/Header';
import { UsageBreakdownCard } from '../components/UsageBreakdownCard';
import { SupplierEvidenceCard } from '../components/SupplierEvidenceCard';
import { DollarSign, RefreshCw, Info, Building2 } from 'lucide-react';
import { api } from '../lib/api';
import type { LegacyOutboundEstimate } from '../lib/api';
import { formatLegacyEstimate, formatRecordedCount, sumRecordedValues } from '../lib/usage-evidence';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';

// Shape returned by GET /admin/usage/breakdown?group_by=tenant
interface TenantUsageRow {
    tenant_id: string;
    tenant_name: string;
    call_count: number;
    total_minutes: number;
    total_seconds: number;
    total_cost: number | null;
    legacy_outbound_estimate: LegacyOutboundEstimate;
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
    const generation = useAuthGeneration();
    return <UsageCostContent key={generation} generation={generation} />;
}

function UsageCostContent({ generation }: { generation: number }) {
    const [fromDate, setFromDate] = useState(monthStart());
    const [toDate, setToDate] = useState(today());
    const [tenantRows, setTenantRows] = useState<TenantUsageRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [monetaryNote, setMonetaryNote] = useState(
        'Authoritative inbound monetary totals are ledger-only and are not shown here.',
    );
    // Bumped on Refresh / date change to force the summary card to refetch.
    const [reloadKey, setReloadKey] = useState(0);
    const [supplierLocked, setSupplierLocked] = useState(false);
    const [resolvedQuery, setResolvedQuery] = useState<string | null>(null);
    const requestGeneration = useRef(0);
    const queryKey = `${fromDate}|${toDate}|${reloadKey}`;
    const captureOwner = useOperationOwner(generation, queryKey);

    const fetchTenantBreakdown = useCallback(async () => {
        const owns = captureOwner();
        const generation = ++requestGeneration.current;
        setLoading(true);
        setError(null);
        try {
            const res = await api.getUsageBreakdown({
                group_by: 'tenant',
                from_date: fromDate,
                to_date: toDate,
            });
            if (!owns() || generation !== requestGeneration.current) return;
            const validRows = res.data && Array.isArray(res.data.breakdown)
                && typeof res.data.monetary_note === 'string' && res.data.breakdown.every(row =>
                    row && typeof row.tenant_id === 'string' && (row.tenant_name === null || typeof row.tenant_name === 'string')
                    && [row.call_count, row.total_minutes, row.total_seconds].every(value =>
                        value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0)));
            if (res.error || !res.data || !validRows) {
                setError('Usage data is unavailable. Please retry.');
                setTenantRows([]);
            } else {
                const rows = [...res.data.breakdown] as unknown as TenantUsageRow[];
                if (res.data) {
                    setMonetaryNote(res.data.monetary_note);
                }
                // Highest spend / usage first.
                rows.sort((a, b) => (b.total_minutes ?? 0) - (a.total_minutes ?? 0));
                setTenantRows(rows);
            }
        } catch {
            if (!owns() || generation !== requestGeneration.current) return;
            setError('Usage data is unavailable. Please retry.');
            setTenantRows([]);
        } finally {
            if (owns() && generation === requestGeneration.current) {
                setLoading(false);
                setResolvedQuery(queryKey);
            }
        }
    }, [fromDate, toDate, queryKey, captureOwner]);

    useEffect(() => {
        fetchTenantBreakdown();
        return () => { requestGeneration.current += 1; };
    }, [fetchTenantBreakdown, reloadKey]);

    const refresh = () => { if (!supplierLocked) setReloadKey((k) => k + 1); };

    const tenantTotalSeconds = sumRecordedValues(tenantRows.map((r) => r.total_seconds));
    const tenantTotalMinutes = tenantTotalSeconds === null ? null : Math.floor(tenantTotalSeconds / 60);
    const tenantTotalCalls = sumRecordedValues(tenantRows.map((r) => r.call_count));
    const currentPeriodLoaded = !loading && resolvedQuery === queryKey;

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
                            <p className="page-description">Review recorded usage and retained supplier evidence</p>
                        </div>
                        <div className="page-header-actions">
                            <div className="date-range-filter">
                                <label>
                                    From
                                    <input
                                        type="date"
                                        value={fromDate}
                                        max={toDate}
                                        disabled={supplierLocked}
                                        onChange={(e) => { if (!supplierLocked) setFromDate(e.target.value); }}
                                    />
                                </label>
                                <label>
                                    To
                                    <input
                                        type="date"
                                        value={toDate}
                                        min={fromDate}
                                        max={today()}
                                        disabled={supplierLocked}
                                        onChange={(e) => { if (!supplierLocked) setToDate(e.target.value); }}
                                    />
                                </label>
                            </div>
                            <button className="btn btn-secondary" onClick={refresh} disabled={loading || supplierLocked}>
                                <RefreshCw size={16} className={loading ? 'spinning' : ''} />
                                Refresh
                            </button>
                        </div>
                    </div>

                    <div className="usage-disclaimer">
                        <Info size={15} />
                        <span>
                            Recorded duration and call counts include inbound and outbound activity.
                            Complete supplier spending and call-level provider attribution are unavailable. {monetaryNote}
                        </span>
                    </div>

                    {currentPeriodLoaded && error && (
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

                    <SupplierEvidenceCard fromDate={fromDate} toDate={toDate} generation={generation}
                        refreshVersion={reloadKey} onLockedChange={setSupplierLocked} />

                    {/* Per-tenant breakdown */}
                    <div className="card">
                        <div className="card-header">
                            <h3 className="card-title">
                                <Building2 size={18} />
                                Usage by Tenant
                            </h3>
                            <span className="card-count">{currentPeriodLoaded ? `${tenantRows.length} tenants` : 'Loading…'}</span>
                        </div>
                        <div className="card-body">
                            <div className="table-container">
                                {!currentPeriodLoaded ? (
                                    <div className="table-loading">
                                        <RefreshCw className="spinning" size={20} />
                                        <span>Loading tenant usage…</span>
                                    </div>
                                ) : error ? (
                                    <p>Tenant usage is unavailable.</p>
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
                                                <th style={{ textAlign: 'right' }}>
                                                    Legacy outbound USD estimate
                                                </th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {tenantRows.map((r) => (
                                                <tr key={r.tenant_id}>
                                                    <td>{r.tenant_name || 'Unknown'}</td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        {formatRecordedCount(r.call_count)}
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        {formatRecordedCount(r.total_minutes)}
                                                    </td>
                                                    <td style={{ textAlign: 'right' }}>
                                                        {formatLegacyEstimate(r.legacy_outbound_estimate)}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                        <tfoot>
                                            <tr>
                                                <td><strong>Total</strong></td>
                                                <td style={{ textAlign: 'right' }}>
                                                    <strong>{formatRecordedCount(tenantTotalCalls)}</strong>
                                                </td>
                                                <td style={{ textAlign: 'right' }}>
                                                    <strong>{formatRecordedCount(tenantTotalMinutes)}</strong>
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
