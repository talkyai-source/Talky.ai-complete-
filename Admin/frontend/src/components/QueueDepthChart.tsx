import { useState, useEffect, useCallback } from 'react';
import { Layers, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import type { QueuesResponse, QueueStatus } from '../lib/api';

interface QueueBarProps {
    queue: QueueStatus;
}

function QueueBar({ queue }: QueueBarProps) {
    const total = queue.pending + queue.processing + queue.failed;
    const maxWidth = 100; // percentage

    // Calculate widths as percentages of total (or use fixed scale)
    const scale = Math.max(total, 1);
    const pendingWidth = Math.min((queue.pending / scale) * maxWidth, 100);
    const processingWidth = Math.min((queue.processing / scale) * maxWidth, 100);

    return (
        <div className="queue-bar-container">
            <div className="queue-bar-header">
                <span className="queue-name">{queue.name}</span>
                <span className="queue-stats">
                    <span className="stat pending">{queue.pending} pending</span>
                    <span className="stat processing">{queue.processing} processing</span>
                    {queue.failed > 0 && <span className="stat failed">{queue.failed} failed</span>}
                </span>
            </div>
            <div className="queue-bar">
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
            </div>
            <div className="queue-bar-footer">
                <span className="success-rate">
                    {queue.success_rate_24h.toFixed(1)}% success (24h)
                </span>
                <span className="avg-time">
                    ~{queue.avg_processing_time_ms}ms avg
                </span>
            </div>
        </div>
    );
}

export function QueueDepthChart() {
    const [data, setData] = useState<QueuesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchQueues = useCallback(async () => {
        try {
            const response = await api.getQueues();
            if (response.data) {
                setData(response.data);
                setError(null);
            } else if (response.error) {
                setError(response.error.message);
            }
        } catch (err) {
            setError('Failed to fetch queue data');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchQueues();
        // Auto-refresh every 10 seconds
        const interval = setInterval(fetchQueues, 10000);
        return () => clearInterval(interval);
    }, [fetchQueues]);

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
                        <span>Failed to load queues</span>
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
                        {data.total_pending} total pending
                    </span>
                    {data.total_processing > 0 && (
                        <span className="badge badge-warning">
                            {data.total_processing} processing
                        </span>
                    )}
                </div>
            </div>
            <div className="card-body queue-chart-body">
                {data.queues.map((queue) => (
                    <QueueBar key={queue.name} queue={queue} />
                ))}
            </div>
        </div>
    );
}
