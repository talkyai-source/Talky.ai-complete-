import { useEffect, useState } from 'react';
import { api, type UsageSummaryResponse } from '../lib/api';
import { formatRecordedCount } from '../lib/usage-evidence';

export function QuotaUsage() {
    const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                const res = await api.getUsageSummary();
                if (cancelled) return;
                setSummary(res.error ? null : res.data ?? null);
                setError(Boolean(res.error || !res.data));
            } catch {
                if (!cancelled) { setSummary(null); setError(true); }
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

    return <QuotaUsageView summary={summary} loading={loading} error={error} />;
}

export function QuotaUsageView({ summary, loading = false, error = false }: {
    summary: UsageSummaryResponse | null; loading?: boolean; error?: boolean;
}) {
    const items = [
        {
            label: 'Recorded call minutes',
            color: 'blue' as const,
            value: formatRecordedCount(summary?.total_call_minutes),
        },
        {
            label: 'Action records',
            color: 'orange' as const,
            value: formatRecordedCount(summary?.total_action_records),
        },
        {
            label: 'Supplier cost',
            color: 'green' as const,
            value: 'Unavailable',
        },
    ];

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Recorded Usage</h3>
            </div>
            <div className="card-body">
                {loading && !summary && (
                    <div style={{ color: 'var(--muted-foreground, #6B7280)' }}>Loading…</div>
                )}
                {error && <p role="alert">Usage data is unavailable. Please retry.</p>}
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
