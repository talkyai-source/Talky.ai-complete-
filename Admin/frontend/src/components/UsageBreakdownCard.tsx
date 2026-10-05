import { useState, useEffect } from 'react';
import {
    DollarSign,
    TrendingUp,
    Phone,
    RefreshCw,
    Info,
} from 'lucide-react';
import { api } from '../lib/api';
import type { UsageSummaryResponse } from '../lib/api';
import { formatLegacyEstimate, formatRecordedCount } from '../lib/usage-evidence';

interface UsageBreakdownCardProps {
    tenantId?: string;
    fromDate?: string;
    toDate?: string;
}

export function UsageBreakdownCard({ tenantId, fromDate, toDate }: UsageBreakdownCardProps) {
    const requestKey = JSON.stringify([tenantId, fromDate, toDate]);
    const [result, setResult] = useState<{
        key: string; summary: UsageSummaryResponse | null; error: string | null;
    } | null>(null);
    const loading = result?.key !== requestKey;
    const summary = loading ? null : result?.summary;
    const error = loading ? null : result?.error;
    useEffect(() => {
        let cancelled = false;
        void api.getUsageSummary({ tenant_id: tenantId, from_date: fromDate, to_date: toDate })
            .then((response) => {
                if (cancelled) return;
                const unavailable = Boolean(response.error || !response.data);
                setResult({ key: requestKey, summary: unavailable ? null : response.data ?? null,
                    error: unavailable ? 'Usage data is unavailable. Please retry.' : null });
            })
            .catch(() => {
                if (!cancelled) setResult({ key: requestKey, summary: null,
                    error: 'Usage data is unavailable. Please retry.' });
            });
        return () => { cancelled = true; };
    }, [tenantId, fromDate, toDate, requestKey]);

    if (loading) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Usage & Cost Breakdown</h3>
                </div>
                <div className="card-body">
                    <div className="loading-state">
                        <RefreshCw className="spinner" size={24} />
                        <span>Loading usage data...</span>
                    </div>
                </div>
            </div>
        );
    }

    if (!summary) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title">Usage & Cost Breakdown</h3>
                </div>
                <div className="card-body">
                    <div className="empty-state">
                        <DollarSign size={32} />
                        <p role="alert">{error || 'Usage data is unavailable. Please retry.'}</p>
                    </div>
                </div>
            </div>
        );
    }

    return <UsageSummaryView summary={summary} />;
}

export function UsageSummaryView({ summary }: { summary: UsageSummaryResponse }) {
    return (
        <div className="card usage-card">
            <div className="card-header">
                <h3 className="card-title">
                    <DollarSign size={18} />
                    Usage & Cost Breakdown
                </h3>
                <span className="period-badge">
                    {summary.period_start} — {summary.period_end}
                </span>
            </div>
            <div className="card-body">
                {/* Summary Stats */}
                <div className="usage-summary-grid">
                    <div className="usage-stat">
                        <div className="stat-icon cost">
                            <DollarSign size={20} />
                        </div>
                        <div className="stat-content">
                            <span className="stat-value">
                                Unavailable
                            </span>
                            <span className="stat-label">Supplier cost</span>
                        </div>
                    </div>
                    <div className="usage-stat">
                        <div className="stat-icon calls">
                            <Phone size={20} />
                        </div>
                        <div className="stat-content">
                            <span className="stat-value">{formatRecordedCount(summary.total_call_minutes)}</span>
                            <span className="stat-label">Recorded call minutes</span>
                        </div>
                    </div>
                    <div className="usage-stat">
                        <div className="stat-icon api">
                            <TrendingUp size={20} />
                        </div>
                        <div className="stat-content">
                            <span className="stat-value">{formatRecordedCount(summary.total_action_records)}</span>
                            <span className="stat-label">Action records</span>
                        </div>
                    </div>
                </div>

                <div className="provider-breakdown">
                    <h4 className="breakdown-title">Provider attribution unavailable</h4>
                    <p>Recorded calls and action records do not establish provider usage or supplier charges.</p>
                    <p>Legacy outbound USD estimate: {formatLegacyEstimate(summary.legacy_outbound_estimate)}</p>
                </div>
                <div className="usage-disclaimer">
                    <Info size={15} />
                    <span>{summary.monetary_note}</span>
                </div>
            </div>
        </div>
    );
}
