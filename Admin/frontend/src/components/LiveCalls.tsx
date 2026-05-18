import { useState, useEffect, useCallback } from 'react';
import { Loader2, Play, Pause } from 'lucide-react';
import { api } from '../lib/api';

interface Call {
    id: string;
    tenant: string;
    agent: string;
    status: 'in-progress' | 'queued' | 'failed';
    duration: string;
}

function formatDuration(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds <= 0) return '-';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

function mapApiStatus(s: string): Call['status'] {
    if (s === 'in_progress' || s === 'initiated' || s === 'ringing') return 'in-progress';
    if (s === 'queued') return 'queued';
    return 'failed';
}

function StatusBadge({ status }: { status: Call['status'] }) {
    const statusConfig = {
        'in-progress': { label: 'In Progress', className: 'in-progress', dotClass: 'green' },
        'queued': { label: 'Queued', className: 'queued', dotClass: 'orange' },
        'failed': { label: 'Failed', className: 'failed', dotClass: 'red' },
    };

    const config = statusConfig[status];

    return (
        <span className={`status-badge ${config.className}`}>
            <span className={`status-dot ${config.dotClass}`}></span>
            {config.label}
        </span>
    );
}

export function LiveCalls() {
    const [calls, setCalls] = useState<Call[]>([]);
    const [isPaused, setIsPaused] = useState(false);
    const [pauseLoading, setPauseLoading] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);

    // Fetch pause status on mount
    useEffect(() => {
        const fetchPauseStatus = async () => {
            try {
                const response = await api.getPauseStatus();
                if (response.data) {
                    setIsPaused(response.data.paused);
                }
            } catch (err) {
                console.warn('Failed to fetch pause status:', err);
            }
        };
        fetchPauseStatus();
    }, []);

    // Poll the real /admin/calls/live endpoint every 5s. The previous
    // version of this widget rendered a hardcoded list of fake calls
    // (ACME / Beta Corp / etc.) — that's gone.
    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                const response = await api.getLiveCalls();
                if (cancelled) return;
                const items = response.data ?? [];
                setCalls(
                    items.map((c) => ({
                        id: c.id,
                        tenant: c.tenant_name || c.tenant_id || '—',
                        agent: c.campaign_name || 'AI Bot',
                        status: mapApiStatus(c.status),
                        duration: formatDuration(c.duration_seconds),
                    })),
                );
            } catch {
                if (!cancelled) setCalls([]);
            }
        };
        void fetchOnce();
        const id = window.setInterval(fetchOnce, 5_000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, []);

    const handlePauseToggle = useCallback(async () => {
        if (!isPaused && !showConfirm) {
            // Show confirmation before pausing
            setShowConfirm(true);
            return;
        }

        setPauseLoading(true);
        try {
            const response = await api.pauseAllCalls();
            if (response.data) {
                setIsPaused(response.data.paused);
            }
        } catch (err) {
            console.error('Failed to toggle pause:', err);
        } finally {
            setPauseLoading(false);
            setShowConfirm(false);
        }
    }, [isPaused, showConfirm]);

    const handleCancelConfirm = useCallback(() => {
        setShowConfirm(false);
    }, []);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Live Calls</h3>
                <div className="pause-controls">
                    {showConfirm ? (
                        <div className="confirm-dialog">
                            <span>Pause all calls?</span>
                            <button
                                className="btn btn-confirm-yes"
                                onClick={handlePauseToggle}
                                disabled={pauseLoading}
                            >
                                Yes
                            </button>
                            <button
                                className="btn btn-confirm-no"
                                onClick={handleCancelConfirm}
                            >
                                No
                            </button>
                        </div>
                    ) : (
                        <button
                            className={`btn ${isPaused ? 'btn-resume' : 'btn-pause'}`}
                            onClick={handlePauseToggle}
                            disabled={pauseLoading}
                        >
                            {pauseLoading ? (
                                <Loader2 className="animate-spin" size={14} />
                            ) : isPaused ? (
                                <>
                                    <Play size={14} />
                                    Resume Calls
                                </>
                            ) : (
                                <>
                                    <Pause size={14} />
                                    Pause All Calls
                                </>
                            )}
                        </button>
                    )}
                </div>
            </div>

            {isPaused && (
                <div className="pause-banner">
                    System is paused. No new calls will be initiated.
                </div>
            )}

            <div className="card-body">
                <table className="table">
                    <thead>
                        <tr>
                            <th>Call ID</th>
                            <th>Tenant</th>
                            <th>Agent</th>
                            <th>Status</th>
                            <th>Duration</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {calls.map((call, index) => (
                            <tr key={`${call.id}-${index}`}>
                                <td>{call.id}</td>
                                <td>{call.tenant}</td>
                                <td>{call.agent}</td>
                                <td>
                                    <StatusBadge status={call.status} />
                                </td>
                                <td>{call.duration}</td>
                                <td>
                                    <button className="btn btn-end">End Call</button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
