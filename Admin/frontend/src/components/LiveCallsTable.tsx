import { useState, useEffect, useCallback } from 'react';
import { Phone, PhoneOff, RefreshCw, Clock, Building2 } from 'lucide-react';
import { api } from '../lib/api';
import type { LiveCallItem } from '../lib/api';

interface LiveCallsTableProps {
    onRefresh?: () => void;
}

function formatDuration(seconds: number): string {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function StatusBadge({ status }: { status: string }) {
    const statusConfig: Record<string, { label: string; className: string }> = {
        'in_progress': { label: 'In Progress', className: 'status-in-progress' },
        'ringing': { label: 'Ringing', className: 'status-ringing' },
        'queued': { label: 'Queued', className: 'status-queued' },
        'initiated': { label: 'Initiated', className: 'status-initiated' },
    };

    const config = statusConfig[status] || { label: status, className: '' };

    return (
        <span className={`call-status-badge ${config.className}`}>
            {config.label}
        </span>
    );
}

export function LiveCallsTable({ onRefresh }: LiveCallsTableProps) {
    const [calls, setCalls] = useState<LiveCallItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [terminatingId, setTerminatingId] = useState<string | null>(null);
    const [confirmTerminate, setConfirmTerminate] = useState<string | null>(null);

    const fetchLiveCalls = useCallback(async () => {
        try {
            const response = await api.getLiveCalls();
            if (response.data) {
                setCalls(response.data);
            }
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to fetch live calls');
        } finally {
            setLoading(false);
        }
    }, []);

    // Initial fetch and auto-refresh every 10 seconds
    useEffect(() => {
        fetchLiveCalls();
        const intervalId = setInterval(fetchLiveCalls, 10000);
        return () => clearInterval(intervalId);
    }, [fetchLiveCalls]);

    const handleTerminate = async (callId: string) => {
        if (confirmTerminate !== callId) {
            setConfirmTerminate(callId);
            return;
        }

        setTerminatingId(callId);
        try {
            await api.terminateCall(callId);
            await fetchLiveCalls();
            onRefresh?.();
        } catch (err) {
            console.error('Failed to terminate call:', err);
        } finally {
            setTerminatingId(null);
            setConfirmTerminate(null);
        }
    };

    const cancelTerminate = () => {
        setConfirmTerminate(null);
    };

    if (loading) {
        return (
            <div className="table-loading">
                <div className="loading-spinner"></div>
                <p>Loading live calls...</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="error-banner">
                <p>{error}</p>
                <button onClick={fetchLiveCalls}>Retry</button>
            </div>
        );
    }

    if (calls.length === 0) {
        return (
            <div className="empty-state">
                <Phone size={48} />
                <h3>No Active Calls</h3>
                <p>There are currently no calls in progress.</p>
            </div>
        );
    }

    return (
        <div className="table-container">
            <table className="data-table">
                <thead>
                    <tr>
                        <th>Call ID</th>
                        <th>Tenant</th>
                        <th>Phone Number</th>
                        <th>Campaign</th>
                        <th>Status</th>
                        <th>Duration</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {calls.map((call) => (
                        <tr key={call.id}>
                            <td className="call-id-cell">{call.id.substring(0, 8)}...</td>
                            <td>
                                <div className="tenant-name-cell">
                                    <Building2 size={14} />
                                    <span>{call.tenant_name}</span>
                                </div>
                            </td>
                            <td className="phone-cell">{call.phone_number}</td>
                            <td>{call.campaign_name || '-'}</td>
                            <td><StatusBadge status={call.status} /></td>
                            <td>
                                <div className="duration-cell">
                                    <Clock size={14} />
                                    <span>{formatDuration(call.duration_seconds)}</span>
                                </div>
                            </td>
                            <td>
                                {confirmTerminate === call.id ? (
                                    <div className="confirm-inline">
                                        <span>Terminate?</span>
                                        <button
                                            className="btn btn-danger btn-sm"
                                            onClick={() => handleTerminate(call.id)}
                                            disabled={terminatingId === call.id}
                                        >
                                            {terminatingId === call.id ? '...' : 'Yes'}
                                        </button>
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={cancelTerminate}
                                        >
                                            No
                                        </button>
                                    </div>
                                ) : (
                                    <button
                                        className="btn btn-danger btn-sm"
                                        onClick={() => handleTerminate(call.id)}
                                    >
                                        <PhoneOff size={14} />
                                        End
                                    </button>
                                )}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <div className="table-footer">
                <span className="calls-count">{calls.length} active call{calls.length !== 1 ? 's' : ''}</span>
                <button className="btn btn-secondary btn-sm" onClick={fetchLiveCalls}>
                    <RefreshCw size={14} />
                    Refresh
                </button>
            </div>
        </div>
    );
}
