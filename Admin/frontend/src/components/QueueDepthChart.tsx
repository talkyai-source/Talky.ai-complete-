import { Layers, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { validQueues } from '../lib/health-contracts';
import { metric, observedAt } from '../lib/health-display';
import type { QueueStatus } from '../lib/api';

interface QueueBarProps {
    queue: QueueStatus;
}

function QueueBar({ queue }: QueueBarProps) {
    const measurable = typeof queue.pending === 'number' && Number.isFinite(queue.pending) && queue.pending >= 0
        && typeof queue.processing === 'number' && Number.isFinite(queue.processing) && queue.processing >= 0;
    const total = (queue.pending ?? 0) + (queue.processing ?? 0);
    const maxWidth = 100; // percentage

    // Calculate widths as percentages of total (or use fixed scale)
    const scale = Math.max(total, 1);
    const pendingWidth = Math.min(((queue.pending ?? 0) / scale) * maxWidth, 100);
    const processingWidth = Math.min(((queue.processing ?? 0) / scale) * maxWidth, 100);

    return (
        <div className="queue-bar-container">
            <div className="queue-bar-header">
                <span className="queue-name">{queue.name}</span>
                <span className="queue-stats">
                    <span className="stat pending">{metric(queue.pending)} pending</span>
                    <span className="stat processing">{metric(queue.processing)} processing</span>
                    {queue.failed !== null && queue.failed > 0 && <span className="stat failed">{queue.failed} failed</span>}
                </span>
            </div>
            <p className="text-muted">{queue.scope} · {queue.status}. {queue.detail}</p>
            {measurable && <div className="queue-bar" aria-label={`${queue.name}: ${queue.pending} pending, ${queue.processing} processing`}>
                <div
                    className="queue-bar-segment pending"
                    style={{ width: `${pendingWidth}%` }}
                    title={`${queue.pending} pending`}
                />
                <div
                    className="queue-bar-segment processing"
                    style={{ width: `${processingWidth}%` }}
                    title={`${queue.processing} processing`}
                />
            </div>}
            <div className="queue-bar-footer">
                <span className="success-rate">
                    Success (24h): {metric(queue.success_rate_24h, '%', 1)}
                </span>
                <span className="avg-time">
                    Average processing time: {metric(queue.avg_processing_time_ms, 'ms')}
                </span>
            </div>
        </div>
    );
}

const loadQueueDepthChart = () => api.getQueues();

export function QueueDepthChart() {
    const { data: data, loading, error, refresh: fetchQueues } = useAdminRead(loadQueueDepthChart, 10000, validQueues);

    if (loading) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title"><Layers size={18} /> Queue Depths</h3>
                </div>
                <div className="card-body">
                    <div className="loading-state">
                        <Loader2 className="animate-spin" size={24} />
                        <span>Loading queues...</span>
                    </div>
                </div>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title"><Layers size={18} /> Queue Depths</h3>
                </div>
                <div className="card-body">
                    <div className="error-state">
                        <AlertCircle size={24} />
                        <span role="alert">Queue observations unavailable: {error ?? 'No data returned'}</span>
                        <button className="btn btn-sm btn-secondary" onClick={fetchQueues}>
                            <RefreshCw size={14} /> Retry
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title"><Layers size={18} /> Queue Depths</h3>
                <div className="card-header-actions">
                    <span className="badge badge-outline">
                        {metric(data.total_pending)} total pending
                    </span>
                    {data.total_processing !== null && data.total_processing > 0 && (
                        <span className="badge badge-warning">
                            {data.total_processing} processing
                        </span>
                    )}
                </div>
            </div>
            <div className="card-body queue-chart-body">
                <p className="text-muted">Observed {observedAt(data.checked_at)}. Counts are limited to each listed source.</p>
                {data.queues.map((queue) => (
                    <QueueBar key={queue.name} queue={queue} />
                ))}
            </div>
        </div>
    );
}
