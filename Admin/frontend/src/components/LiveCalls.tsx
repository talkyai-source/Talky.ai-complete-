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

// Mock data - will be replaced with actual API call when endpoint is available
const mockCalls: Call[] = [
    { id: '123456', tenant: 'ACME Inc.', agent: 'AI Bot', status: 'in-progress', duration: '2:34' },
    { id: '123469', tenant: 'ACME Inc.', agent: 'AI Bot', status: 'in-progress', duration: '1:15' },
    { id: '123463', tenant: 'ACME Inc.', agent: 'AI Bot', status: 'queued', duration: '-' },
    { id: '123456', tenant: 'ACME Inc.', agent: 'John D', status: 'in-progress', duration: '0:45' },
    { id: '123458', tenant: 'Beta Corp.', agent: 'AI Bot', status: 'failed', duration: '-' },
];

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
    const [calls] = useState<Call[]>(mockCalls);
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
