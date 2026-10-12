import { useState, useEffect, useCallback, useRef } from 'react';
import { Loader2, Play, Pause } from 'lucide-react';
import { api } from '../lib/api';
import type { LiveCallItem } from '../lib/api';
import { CallTerminationAction } from './CallTerminationAction';
import { getTerminationPhase, mergePolledLiveCalls } from '../lib/call-termination';

function formatDuration(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds <= 0) return '-';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
}

type DisplayStatus = 'in-progress' | 'queued' | 'failed' | 'ending' | 'end-failed' | 'ended';

function mapApiStatus(call: LiveCallItem): DisplayStatus {
    const terminationPhase = getTerminationPhase(call);
    if (terminationPhase === 'requested') return 'ending';
    if (terminationPhase === 'failed') return 'end-failed';
    if (terminationPhase === 'confirmed') return 'ended';

    const s = call.status;
    if (['in_progress', 'initiated', 'dialing', 'ringing', 'answered', 'in_call'].includes(s)) {
        return 'in-progress';
    }
    if (s === 'queued') return 'queued';
    return 'failed';
}

function StatusBadge({ call }: { call: LiveCallItem }) {
    const status = mapApiStatus(call);
    const statusConfig = {
        'in-progress': { label: 'In Progress', className: 'in-progress', dotClass: 'green' },
        'queued': { label: 'Queued', className: 'queued', dotClass: 'orange' },
        'failed': { label: 'Failed', className: 'failed', dotClass: 'red' },
        'ending': { label: 'Ending', className: 'ending', dotClass: 'orange' },
        'end-failed': { label: 'End failed', className: 'end-failed', dotClass: 'red' },
        'ended': { label: 'Ended', className: 'ended', dotClass: 'gray' },
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
    const [calls, setCalls] = useState<LiveCallItem[]>([]);
    const [isPaused, setIsPaused] = useState(false);
    const [pauseLoading, setPauseLoading] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);
    const [pauseReason, setPauseReason] = useState('');
    const [pollError, setPollError] = useState<string | null>(null);
    // Set while the pause state could not be read (or a pause/resume failed).
    // isPaused alone cannot say "unknown": it would read as "not paused".
    const [pauseStatusError, setPauseStatusError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    // Bumped by every pause/resume write so a status read that started before
    // the write cannot overwrite the newer answer.
    const pauseWriteSeq = useRef(0);

    const refreshPauseStatus = useCallback(async () => {
        const seq = pauseWriteSeq.current;
        try {
            const response = await api.getPauseStatus();
            if (response.error) throw new Error(response.error.message);
            if (seq !== pauseWriteSeq.current) return;
            if (response.data) {
                setIsPaused(response.data.paused);
                // Only adopt the server's reason while paused: when not paused
                // the field doubles as the operator's draft for the next pause.
                if (response.data.paused) setPauseReason(response.data.reason || '');
                setPauseStatusError(null);
            }
        } catch (err) {
            console.warn('Failed to fetch pause status:', err);
            if (seq !== pauseWriteSeq.current) return;
            setPauseStatusError(err instanceof Error ? err.message : 'Could not read the pause status.');
        }
    }, []);

    // Fetch pause status on mount
    useEffect(() => {
        void refreshPauseStatus();
    }, [refreshPauseStatus]);

    // Poll the real /admin/calls/live endpoint every 5s. The previous
    // version of this widget rendered a hardcoded list of fake calls
    // (ACME / Beta Corp / etc.) — that's gone.
    useEffect(() => {
        let cancelled = false;
        const fetchOnce = async () => {
            try {
                const response = await api.getLiveCalls();
                if (response.error) throw new Error(response.error.message);
                if (cancelled) return;
                const items = response.data ?? [];
                setCalls((current) => mergePolledLiveCalls(current, items));
                setPollError(null);
            } catch (error) {
                if (!cancelled) {
                    setPollError(error instanceof Error
                        ? error.message
                        : 'Live-call refresh failed. Existing statuses may be stale.');
                }
            }
        };
        void fetchOnce();
        const tick = () => {
            void fetchOnce();
            // A kill switch flipped elsewhere (or a failed first read) must not stay stale.
            void refreshPauseStatus();
        };
        const id = window.setInterval(tick, 5_000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, [refreshPauseStatus]);

    const patchCall = useCallback((callId: string, patch: Partial<LiveCallItem>) => {
        setCalls((current) => current.map((call) => (
            call.id === callId ? { ...call, ...patch } : call
        )));
    }, []);

    const confirmCallEnded = useCallback((callId: string) => {
        setCalls((current) => current.filter((call) => call.id !== callId));
    }, []);

    const handlePauseToggle = useCallback(async () => {
        if (!isPaused && !showConfirm) {
            // Show confirmation before pausing
            setShowConfirm(true);
            return;
        }

        setPauseLoading(true);
        setActionError(null);
        pauseWriteSeq.current += 1;
        try {
            const shouldPause = !isPaused;
            const response = await api.setPauseAllCalls(
                shouldPause,
                shouldPause ? (pauseReason || 'Paused from Admin Command Center') : undefined,
            );
            if (response.error) throw new Error(response.error.message);
            if (response.data) {
                setIsPaused(response.data.paused);
                setPauseReason(response.data.reason || '');
                setPauseStatusError(null);
            }
            setShowConfirm(false);
        } catch (err) {
            console.error('Failed to toggle pause:', err);
            // Leave the confirm open: the emergency stop was NOT applied.
            setActionError(err instanceof Error
                ? err.message
                : 'The request failed. Calls were not paused or resumed.');
        } finally {
            setPauseLoading(false);
        }
    }, [isPaused, showConfirm, pauseReason]);

    const handleCancelConfirm = useCallback(() => {
        setShowConfirm(false);
        setActionError(null);
    }, []);

    return (
        <div className="card">
            <div className="card-header">
                <h3 className="card-title">Live Calls</h3>
                <div className="pause-controls">
                    {showConfirm ? (
                        <div className="confirm-dialog">
                            <span>Pause all calls?</span>
                            <input
                                className="pause-reason-input"
                                value={pauseReason}
                                onChange={(event) => setPauseReason(event.target.value)}
                                placeholder="Reason (optional)"
                                maxLength={500}
                                aria-label="Reason for pausing outbound calls"
                            />
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

            {actionError && (
                <div className="error-banner inline" role="alert">
                    <p>
                        {isPaused ? 'Calls were NOT resumed' : 'Calls were NOT paused'}: {actionError}
                    </p>
                </div>
            )}

            {pauseStatusError && (
                <div className="error-banner inline" role="alert">
                    <p>
                        Pause status unknown: {pauseStatusError} Calls may or may not be paused.
                    </p>
                </div>
            )}

            {isPaused && (
                <div className="pause-banner">
                    New outbound calls are paused across all workers.
                    {pauseReason && <span> Reason: {pauseReason}</span>}
                </div>
            )}

            {pollError && (
                <div className="error-banner inline" role="alert">
                    <p>{pollError} Existing call statuses remain visible until refresh succeeds.</p>
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
                        {calls.map((call) => (
                            <tr key={call.id}>
                                <td>{call.id}</td>
                                <td>{call.tenant_name || call.tenant_id || '—'}</td>
                                <td>{call.campaign_name || 'AI Bot'}</td>
                                <td>
                                    <StatusBadge call={call} />
                                </td>
                                <td>{formatDuration(call.duration_seconds)}</td>
                                <td>
                                    <CallTerminationAction
                                        call={call}
                                        onPatch={patchCall}
                                        onConfirmed={confirmCallEnded}
                                    />
                                </td>
                            </tr>
                        ))}
                        {calls.length === 0 && (
                            <tr>
                                <td className="table-empty-row" colSpan={6}>No active calls</td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
