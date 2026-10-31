import { useCallback, useState } from 'react';
import { Link } from 'react-router-dom';
import { LifeBuoy } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { PRIORITY_LABELS, SUPPORT_PRIORITIES, validMetrics } from '../lib/support-contracts';
import { metricText } from '../lib/support-format';
import './SupportConsole.css';

/** Support outcomes for the Command Center. Missing figures read as unavailable, never as zero. */
export function SupportMetricsCard() {
    const [days, setDays] = useState<7 | 30>(7);
    const load = useCallback(() => api.getSupportMetrics(days), [days]);
    const { data, error, loading } = useAdminRead(load, 60000, validMetrics);
    const openTotal = data ? SUPPORT_PRIORITIES.reduce((sum, level) => sum + data.open_by_priority[level], 0) : null;
    return (
        <div className="card">
            <div className="card-header">
                <h2 className="card-title"><LifeBuoy size={18} /> Support</h2>
                <div className="support-metrics-toggle" role="group" aria-label="Metrics period">
                    {([7, 30] as const).map((option) => (
                        <button key={option} type="button" className={`btn btn-sm ${days === option ? 'btn-primary' : 'btn-secondary'}`}
                            aria-pressed={days === option} onClick={() => setDays(option)}>{option} days</button>
                    ))}
                </div>
            </div>
            <div className="card-body">
                {error && <div className="error-banner inline" role="alert"><p>Support metrics unavailable: {error}{data ? ' Showing last known values, which may be stale.' : ''}</p></div>}
                {loading && !data && <p role="status">Loading support metrics…</p>}
                {data && (
                    <dl className="support-metrics">
                        <div><dt>Solved by the assistant</dt><dd>{metricText(data.bot_solved_pct, '%')}</dd></div>
                        <div><dt>Conversations</dt><dd>{metricText(data.conversations)}</dd></div>
                        <div><dt>Tickets created</dt><dd>{metricText(data.tickets_created)}</dd></div>
                        <div><dt>Open tickets</dt><dd>{metricText(openTotal)}<small> {SUPPORT_PRIORITIES.map((level) => `${PRIORITY_LABELS[level]} ${data.open_by_priority[level]}`).join(' · ')}</small></dd></div>
                        <div><dt>Missed deadlines</dt><dd>{metricText(data.sla_breaches)}</dd></div>
                        <div><dt>Median first response</dt><dd>{metricText(data.median_first_response_minutes, ' min')}</dd></div>
                        <div><dt>Average rating</dt><dd>{metricText(data.avg_csat, ' / 5')}</dd></div>
                    </dl>
                )}
                <p className="support-note"><Link to="/support">Open the ticket queue</Link></p>
            </div>
        </div>
    );
}
