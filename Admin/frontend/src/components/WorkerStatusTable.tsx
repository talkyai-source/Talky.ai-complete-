import { Users, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { api } from '../lib/api';
import { useAdminRead } from '../lib/use-admin-read';
import { validWorkers } from '../lib/health-contracts';
import { metric, observedAt } from '../lib/health-display';

function StatusDot({ status }: { status: string }) {
    const colors: Record<string, string> = {
        healthy: 'var(--accent-green)',
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

function formatTimeAgo(isoString: string | null): string {
    if (!isoString) return 'No heartbeat';
    const date = new Date(isoString);
    const now = new Date();
    const diff = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (!Number.isFinite(diff) || diff < 0) return 'Invalid heartbeat time';
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
}

function formatUptime(seconds: number | null): string {
    if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return 'Unavailable';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
    return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

const loadWorkerStatusTable = () => api.getWorkers();

export function WorkerStatusTable() {
    const { data: data, loading, error, refresh: fetchWorkers } = useAdminRead(loadWorkerStatusTable, 15000, validWorkers);

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
                        <span role="alert">Worker observations unavailable: {error ?? 'No data returned'}</span>
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
                    <span className="badge badge-outline">{data.active_workers} roles with fresh heartbeats</span>
                    {data.busy_workers !== null && data.busy_workers > 0 && (
                        <span className="badge badge-warning">{data.busy_workers} Busy</span>
                    )}
                </div>
            </div>
            <div className="card-body">
                <p className="text-muted">{data.scope}. Observed {observedAt(data.checked_at)}.</p>
                <div className="table-container" role="region" aria-label="Worker status details" tabIndex={0}>
                <table className="data-table compact">
                    <thead>
                        <tr>
                            <th>Worker role</th>
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
                                    <span className={`badge badge-${worker.status === 'healthy' ? 'success' : worker.status === 'offline' ? 'danger' : 'outline'}`}>
                                        {worker.status.charAt(0).toUpperCase() + worker.status.slice(1)}
                                    </span>
                                    <p className="text-muted">{worker.detail}</p>
                                </td>
                                <td className="task-cell">
                                    {worker.current_task || <span className="text-muted">Unavailable</span>}
                                </td>
                                <td>{metric(worker.processed_count)}</td>
                                <td>
                                    <span>
                                        {metric(worker.success_rate, '%', 1)}
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
        </div>
    );
}
