import { useState, useEffect, useCallback, useRef } from 'react';
import { Phone, RefreshCw, Clock, Building2, PhoneIncoming, PhoneOutgoing } from 'lucide-react';
import { api } from '../lib/api';
import type { LiveCallItem } from '../lib/api';
import { CallTerminationAction } from './CallTerminationAction';
import { getTerminationPhase, mergePolledLiveCalls } from '../lib/call-termination';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';

interface LiveCallsTableProps {
    onRefresh?: () => void;
    onCallSelect?: (callId: string) => void;
}

function formatDuration(seconds: number): string {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function StatusBadge({ call }: { call: LiveCallItem }) {
    const terminationPhase = getTerminationPhase(call);
    if (terminationPhase === 'requested') {
        return <span className="call-status-badge status-ending">Ending</span>;
    }
    if (terminationPhase === 'failed') {
        return <span className="call-status-badge status-end-failed">End failed</span>;
    }
    if (terminationPhase === 'confirmed') {
        return <span className="call-status-badge status-ended">Ended</span>;
    }

    const { status } = call;
    const statusConfig: Record<string, { label: string; className: string }> = {
        'in_progress': { label: 'In Progress', className: 'status-in-progress' },
        'dialing': { label: 'Dialing', className: 'status-ringing' },
        'ringing': { label: 'Ringing', className: 'status-ringing' },
        'answered': { label: 'Answered', className: 'status-in-progress' },
        'in_call': { label: 'In Call', className: 'status-in-progress' },
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

export function LiveCallsTable(props: LiveCallsTableProps) {
    const generation = useAuthGeneration();
    return <OwnedLiveCallsTable key={generation} {...props} generation={generation} />;
}

function OwnedLiveCallsTable({ onRefresh, onCallSelect, generation }: LiveCallsTableProps & { generation: number }) {
    const [calls, setCalls] = useState<LiveCallItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const captureOwner = useOperationOwner(generation, 'live-calls-table');
    const readSequence = useRef(0);
    const pendingRead = useRef<object | null>(null);

    const fetchLiveCalls = useCallback(async (manual = false) => {
        const ownsView = captureOwner();
        if (!ownsView() || (!manual && pendingRead.current)) return;
        const ticket = {};
        pendingRead.current = ticket;
        const sequence = ++readSequence.current;
        const owns = () => ownsView() && sequence === readSequence.current;
        try {
            const response = await api.getLiveCalls();
            if (!owns()) return;
            if (response.error) throw new Error(response.error.message);
            if (!Array.isArray(response.data) || response.data.some(item => !item
                || typeof item.id !== 'string' || !item.id || typeof item.status !== 'string' || !item.status)) {
                throw new Error('The live-call response was incomplete or invalid.');
            }
            if (response.data) {
                setCalls((current) => mergePolledLiveCalls(current, response.data || []));
            }
            setError(null);
        } catch (err) {
            if (!owns()) return;
            setError(err instanceof Error ? err.message : 'Failed to fetch live calls');
        } finally {
            if (pendingRead.current === ticket) pendingRead.current = null;
            if (owns()) setLoading(false);
        }
    }, [captureOwner]);

    // Initial fetch and auto-refresh every 10 seconds
    useEffect(() => {
        fetchLiveCalls();
        const intervalId = window.setInterval(() => void fetchLiveCalls(), 10000);
        return () => { readSequence.current += 1; pendingRead.current = null; window.clearInterval(intervalId); };
    }, [fetchLiveCalls]);

    const patchCall = (callId: string, patch: Partial<LiveCallItem>) => {
        if (!captureOwner()()) return;
        readSequence.current += 1;
        setCalls((current) => current.map((call) => (
            call.id === callId ? { ...call, ...patch } : call
        )));
    };

    const confirmCallEnded = (callId: string) => {
        if (!captureOwner()()) return;
        readSequence.current += 1;
        setCalls((current) => current.filter((call) => call.id !== callId));
        void fetchLiveCalls(true);
        onRefresh?.();
    };

    if (loading) {
        return (
            <div className="table-loading">
                <div className="loading-spinner"></div>
                <p>Loading live calls...</p>
            </div>
        );
    }

    if (error && calls.length === 0) {
        return (
            <div className="error-banner">
                <p>{error}</p>
                <button onClick={() => void fetchLiveCalls(true)}>Retry</button>
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
            {error && (
                <div className="error-banner inline" role="alert">
                    <p>{error} Existing call statuses remain visible until refresh succeeds.</p>
                    <button type="button" onClick={() => void fetchLiveCalls(true)}>Retry</button>
                </div>
            )}
            <table className="data-table clickable-rows">
                <thead>
                    <tr>
                        <th>Call ID</th>
                        <th>Tenant</th>
                        <th>Phone Number</th>
                        <th>Direction / DID</th>
                        <th>Campaign</th>
                        <th>Status</th>
                        <th>Duration</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {calls.map((call) => (
                        <tr
                            key={call.id}
                            className={onCallSelect ? 'clickable-row' : undefined}
                            onClick={() => onCallSelect?.(call.id)}
                            onKeyDown={(event) => {
                                if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
                                    event.preventDefault();
                                    onCallSelect?.(call.id);
                                }
                            }}
                            role={onCallSelect ? 'button' : undefined}
                            tabIndex={onCallSelect ? 0 : undefined}
                            aria-label={onCallSelect ? `Open ${call.direction || 'outbound'} call ${call.id}` : undefined}
                        >
                            <td className="call-id-cell">{call.id.substring(0, 8)}...</td>
                            <td>
                                <div className="tenant-name-cell">
                                    <Building2 size={14} />
                                    <span>{call.tenant_name}</span>
                                </div>
                            </td>
                            <td className="phone-cell">{call.phone_number}</td>
                            <td>
                                <span className={`direction-badge direction-${call.direction || 'outbound'}`}>
                                    {call.direction === 'inbound' ? <PhoneIncoming size={13} /> : <PhoneOutgoing size={13} />}
                                    {call.direction || 'outbound'}
                                </span>
                                {call.called_did && <span className="called-did">DID {call.called_did}</span>}
                            </td>
                            <td>{call.campaign_name || '-'}</td>
                            <td><StatusBadge call={call} /></td>
                            <td>
                                <div className="duration-cell">
                                    <Clock size={14} />
                                    <span>{formatDuration(call.duration_seconds)}</span>
                                </div>
                            </td>
                            <td>
                                <CallTerminationAction
                                    call={call}
                                    onPatch={patchCall}
                                    onConfirmed={confirmCallEnded}
                                />
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <div className="table-footer">
                <span className="calls-count">{calls.length} active call{calls.length !== 1 ? 's' : ''}</span>
                <button className="btn btn-secondary btn-sm" onClick={() => void fetchLiveCalls(true)}>
                    <RefreshCw size={14} />
                    Refresh
                </button>
            </div>
        </div>
    );
}
