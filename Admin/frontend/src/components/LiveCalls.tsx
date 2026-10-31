import { useState, useEffect, useCallback, useRef } from 'react';
import { Loader2, Play, Pause } from 'lucide-react';
import { api } from '../lib/api';
import type { LiveCallItem } from '../lib/api';
import { CallTerminationAction } from './CallTerminationAction';
import { getTerminationPhase, mergePolledLiveCalls } from '../lib/call-termination';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';

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
    const generation = useAuthGeneration();
    return <OwnedLiveCalls key={generation} generation={generation} />;
}

function OwnedLiveCalls({ generation }: { generation: number }) {
    const [calls, setCalls] = useState<LiveCallItem[]>([]);
    const [callsLoading, setCallsLoading] = useState(true);
    const [isPaused, setIsPaused] = useState(false);
    const [pauseLoading, setPauseLoading] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);
    const [pauseReason, setPauseReason] = useState('');
    const [pollError, setPollError] = useState<string | null>(null);
    // Set while the pause state could not be read (or a pause/resume failed).
    // isPaused alone cannot say "unknown": it would read as "not paused".
    const [pauseStatusError, setPauseStatusError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [pauseStatusKnown, setPauseStatusKnown] = useState(false);
    const captureOwner = useOperationOwner(generation, 'live-calls');
    const callsReadSeq = useRef(0);
    const pauseReadSeq = useRef(0);
    const callsPending = useRef<object | null>(null);
    const pausePending = useRef<object | null>(null);
    const pauseBusy = useRef(false);
    // Bumped by every pause/resume write so a status read that started before
    // the write cannot overwrite the newer answer.
    const pauseWriteSeq = useRef(0);

    const refreshPauseStatus = useCallback(async () => {
        const ownsView = captureOwner();
        if (!ownsView() || pauseBusy.current || pausePending.current) return;
        const ticket = {};
        pausePending.current = ticket;
        const seq = pauseWriteSeq.current;
        const read = ++pauseReadSeq.current;
        const owns = () => ownsView() && seq === pauseWriteSeq.current && read === pauseReadSeq.current;
        try {
            const response = await api.getPauseStatus();
            if (!owns()) return;
            if (response.error) throw new Error(response.error.message);
            if (typeof response.data?.paused !== 'boolean') throw new Error('The pause status could not be verified.');
            if (response.data) {
                setIsPaused(response.data.paused);
                // Only adopt the server's reason while paused: when not paused
                // the field doubles as the operator's draft for the next pause.
                if (response.data.paused) setPauseReason(response.data.reason || '');
                setPauseStatusError(null);
                setPauseStatusKnown(true);
            }
        } catch (err) {
            console.warn('Failed to fetch pause status:', err);
            if (!owns()) return;
            setPauseStatusKnown(false);
            setPauseStatusError(err instanceof Error ? err.message : 'Could not read the pause status.');
        } finally {
            if (pausePending.current === ticket) pausePending.current = null;
        }
    }, [captureOwner]);

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
            const ownsView = captureOwner();
            if (!ownsView() || cancelled || callsPending.current) return;
            const ticket = {};
            callsPending.current = ticket;
            const read = ++callsReadSeq.current;
            const owns = () => ownsView() && !cancelled && read === callsReadSeq.current;
            try {
                const response = await api.getLiveCalls();
                if (!owns()) return;
                if (response.error) throw new Error(response.error.message);
                if (!Array.isArray(response.data) || response.data.some(item => !item
                    || typeof item.id !== 'string' || !item.id || typeof item.status !== 'string' || !item.status)) {
                    throw new Error('The live-call response was incomplete or invalid.');
                }
                const items = response.data;
                setCalls((current) => mergePolledLiveCalls(current, items));
                setPollError(null);
            } catch (error) {
                if (owns()) {
                    setPollError(error instanceof Error
                        ? error.message
                        : 'Live-call refresh failed. Existing statuses may be stale.');
                }
            } finally {
                if (callsPending.current === ticket) callsPending.current = null;
                if (owns()) setCallsLoading(false);
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
            callsPending.current = null;
            pausePending.current = null;
            window.clearInterval(id);
        };
    }, [captureOwner, refreshPauseStatus]);

    const patchCall = useCallback((callId: string, patch: Partial<LiveCallItem>) => {
        if (!captureOwner()()) return;
        callsReadSeq.current += 1;
        setCalls((current) => current.map((call) => (
            call.id === callId ? { ...call, ...patch } : call
        )));
    }, [captureOwner]);

    const confirmCallEnded = useCallback((callId: string) => {
        if (!captureOwner()()) return;
        callsReadSeq.current += 1;
        setCalls((current) => current.filter((call) => call.id !== callId));
    }, [captureOwner]);

    const handlePauseToggle = useCallback(async () => {
        const owns = captureOwner();
        if (!owns() || pauseBusy.current || !pauseStatusKnown) return;
        if (showConfirm && isPaused) {
            setShowConfirm(false);
            setActionError('Calls are already paused. Review the current status before another action.');
            return;
        }
        if (!isPaused && !showConfirm) {
            // Show confirmation before pausing
            setShowConfirm(true);
            return;
        }

        pauseBusy.current = true;
        setPauseLoading(true);
        setActionError(null);
        pauseWriteSeq.current += 1;
        pausePending.current = null;
        let rejected = false;
        try {
            const shouldPause = !isPaused;
            const response = await api.setPauseAllCalls(
                shouldPause,
                shouldPause ? (pauseReason || 'Paused from Admin Command Center') : undefined,
            );
            if (!owns()) return;
            if (response.error) {
                rejected = [400, 401, 403, 422].includes(response.error.status ?? 0);
                throw new Error(response.error.message);
            }
            if (response.data?.paused !== shouldPause) throw new Error('The requested pause status was not confirmed.');
            if (response.data) {
                setIsPaused(response.data.paused);
                setPauseReason(response.data.reason || '');
                setPauseStatusError(null);
                setPauseStatusKnown(true);
            }
            setShowConfirm(false);
        } catch (err) {
            if (!owns()) return;
            console.error('Failed to toggle pause:', err);
            const message = err instanceof Error ? err.message : 'The request could not be confirmed.';
            setActionError(rejected ? `Request rejected: ${message}`
                : `${message} The pause/resume outcome is unconfirmed; check current status before another request.`);
            if (!rejected) {
                setPauseStatusKnown(false);
                setPauseStatusError('The latest pause/resume outcome is unconfirmed.');
            }
        } finally {
            if (owns()) {
                pauseBusy.current = false;
                pauseWriteSeq.current += 1;
                setPauseLoading(false);
            }
        }
    }, [captureOwner, isPaused, showConfirm, pauseReason, pauseStatusKnown]);

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
                                disabled={pauseLoading || !pauseStatusKnown}
                            >
                                Yes
                            </button>
                            <button
                                className="btn btn-confirm-no"
                                onClick={handleCancelConfirm}
                                disabled={pauseLoading}
                            >
                                No
                            </button>
                        </div>
                    ) : (
                        <button
                            className={`btn ${isPaused ? 'btn-resume' : 'btn-pause'}`}
                            onClick={handlePauseToggle}
                            disabled={pauseLoading || !pauseStatusKnown}
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
                        {actionError}
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
                    {pauseStatusKnown
                        ? 'New outbound calls are paused across all workers.'
                        : 'Last observed: new outbound calls were paused.'}
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
                                <td className="table-empty-row" colSpan={6}>{pollError ? 'Live calls unavailable' : callsLoading ? 'Loading live calls…' : 'No active calls'}</td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
