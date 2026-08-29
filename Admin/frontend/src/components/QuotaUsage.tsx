import { useEffect, useState } from 'react';
import { api, type UsageSummaryResponse } from '../lib/api';
import { formatCurrencyAmount } from '../lib/call-cost';

// Cluster-wide usage panel. Previously rendered hardcoded percentages
// (Calls 85%, Tokens 45%, Storage 30%) — those are gone. We now pull
// real totals from /admin/usage/summary and surface the raw values; a
// percentage view requires an explicit cluster quota cap, which is a
// per-tenant configuration rather than a single global number.

function formatNumber(n: number): string {
    if (!Number.isFinite(n)) return '—';
    return Math.round(n).toLocaleString();
}

export function QuotaUsage() {
    const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                const res = await api.getUsageSummary();
                if (cancelled) return;
                setSummary(res.data ?? null);
            } catch {
                if (!cancelled) setSummary(null);
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void fetchOnce();
        const id = window.setInterval(fetchOnce, 60_000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, []);

    const items = [
        {
            label: 'Call minutes',
            color: 'blue' as const,
            value: formatNumber(summary?.total_call_minutes ?? 0),
        },
        {
            label: 'API calls',
            color: 'orange' as const,
            value: formatNumber(summary?.total_api_calls ?? 0),
        },
        {
            label: 'Estimated / legacy cost (inbound ledger excluded)',
            color: 'green' as const,
            value: formatCurrencyAmount(
                summary?.total_cost ?? 0,
                summary?.cost_currency,
                2,
            ),
        },
    ];

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Quota Usage</h3>
            </div>
            <div className="card-body">
                {loading && !summary && (
                    <div style={{ color: 'var(--muted-foreground, #6B7280)' }}>Loading…</div>
                )}
                <div className="quota-chart">
                    <div className="quota-legend">
                        {items.map((item) => (
                            <div className="quota-legend-item" key={item.label}>
                                <div className={`quota-legend-color ${item.color}`}></div>
                                <span>
                                    {item.label}: <strong>{item.value}</strong>
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
