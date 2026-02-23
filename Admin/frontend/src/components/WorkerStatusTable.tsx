import { useState, useEffect, useCallback } from 'react';
import { Users, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import type { WorkersResponse } from '../lib/api';

function StatusDot({ status }: { status: string }) {
    const colors: Record<string, string> = {
        idle: 'var(--accent-green)',
        busy: 'var(--accent-orange)',
        offline: 'var(--accent-red)'
    };
    return (
        <span
            className="status-dot"
            style={{ backgroundColor: colors[status] || 'var(--text-secondary)' }}
            title={status.charAt(0).toUpperCase() + status.slice(1)}
        />
    );
}

function formatTimeAgo(isoString: string): string {
    const date = new Date(isoString);
    const now = new Date();
    const diff = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

function formatUptime(seconds: number): string {
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

export function WorkerStatusTable() {
    const [data, setData] = useState<WorkersResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchWorkers = useCallback(async () => {
        try {
            const response = await api.getWorkers();
            if (response.data) {
                setData(response.data);
                setError(null);
            } else if (response.error) {
                setError(response.error.message);
            }
        } catch (err) {
            setError('Failed to fetch worker data');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchWorkers();
        // Auto-refresh every 15 seconds
        const interval = setInterval(fetchWorkers, 15000);
        return () => clearInterval(interval);
    }, [fetchWorkers]);

    if (loading) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title"><Users size={18} /> Worker Status</h3>
                </div>
                <div className="card-body">
                    <div className="loading-state">
                        <Loader2 className="animate-spin" size={24} />
                        <span>Loading workers...</span>
                    </div>
                </div>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="card">
                <div className="card-header">
                    <h3 className="card-title"><Users size={18} /> Worker Status</h3>
                </div>
                <div className="card-body">
                    <div className="error-state">
                        <AlertCircle size={24} />
                        <span>Failed to load workers</span>
                        <button className="btn btn-sm btn-secondary" onClick={fetchWorkers}>
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
                <h3 className="card-title"><Users size={18} /> Worker Status</h3>
                <div className="card-header-actions">
                    <span className="badge badge-success">{data.active_workers} Active</span>
                    {data.busy_workers > 0 && (
                        <span className="badge badge-warning">{data.busy_workers} Busy</span>
                    )}
                </div>
            </div>
            <div className="card-body">
                <table className="data-table compact">
                    <thead>
                        <tr>
                            <th>Worker</th>
                            <th>Status</th>
                            <th>Current Task</th>
                            <th>Processed</th>
                            <th>Success Rate</th>
                            <th>Uptime</th>
                            <th>Last Heartbeat</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.workers.map((worker) => (
                            <tr key={worker.id}>
                                <td>
                                    <div className="worker-name">
                                        <StatusDot status={worker.status} />
                                        <span>{worker.name}</span>
                                    </div>
                                </td>
                                <td>
                                    <span className={`badge badge-${worker.status === 'idle' ? 'success' : worker.status === 'busy' ? 'warning' : 'danger'}`}>
                                        {worker.status.charAt(0).toUpperCase() + worker.status.slice(1)}
                                    </span>
                                </td>
                                <td className="task-cell">
                                    {worker.current_task || <span className="text-muted">—</span>}
                                </td>
                                <td>{worker.processed_count.toLocaleString()}</td>
                                <td>
                                    <span className={worker.success_rate >= 95 ? 'text-success' : worker.success_rate >= 80 ? 'text-warning' : 'text-danger'}>
                                        {worker.success_rate.toFixed(1)}%
                                    </span>
                                </td>
                                <td>{formatUptime(worker.uptime_seconds)}</td>
                                <td>{formatTimeAgo(worker.last_heartbeat)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
