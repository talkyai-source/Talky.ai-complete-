import { useEffect, useRef, useState } from 'react';
import { Loader2, PhoneOff, RotateCcw } from 'lucide-react';
import { api } from '../lib/api';
import type { LiveCallItem } from '../lib/api';
import {
    classifyTerminationResult,
    getTerminationPhase,
    terminationPatchFromResult,
} from '../lib/call-termination';

interface CallTerminationActionProps {
    call: LiveCallItem;
    onPatch: (callId: string, patch: Partial<LiveCallItem>) => void;
    onConfirmed: (callId: string) => void;
}

type LocalPhase = 'idle' | 'submitting' | 'requested' | 'failed' | 'unknown' | 'confirmed';

function terminationDetails(value: unknown, callId: string): Record<string, unknown> | null {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const details = value as Record<string, unknown>;
    return details.call_id === callId ? details : null;
}

export function CallTerminationAction({
    call,
    onPatch,
    onConfirmed,
}: CallTerminationActionProps) {
    const serverPhase = getTerminationPhase(call);
    const [localPhase, setLocalPhase] = useState<LocalPhase>('idle');
    const [localError, setLocalError] = useState<string | null>(null);
    const [confirming, setConfirming] = useState(false);
    const [checking, setChecking] = useState(false);
    const requestInFlight = useRef(false);
    const selection = useRef<{ active: boolean } | null>(null);
    const session = api.getAuthGeneration();

    useEffect(() => {
        const owner = { active: true };
        selection.current = owner;
        requestInFlight.current = false;
        setLocalPhase('idle');
        setLocalError(null);
        setConfirming(false);
        setChecking(false);
        return () => { owner.active = false; };
    }, [call.id, session]);

    useEffect(() => {
        if (serverPhase === 'requested') {
            setLocalPhase('requested');
            setLocalError(null);
            setConfirming(false);
        } else if (serverPhase === 'failed') {
            // An older poll cannot erase a pending or transport-unknown request.
            setLocalPhase((current) => ['requested', 'unknown', 'submitting'].includes(current) ? current : 'failed');
            setConfirming(false);
        }
    }, [call.termination_error, serverPhase]);

    const effectivePhase: LocalPhase = ['submitting', 'requested', 'unknown'].includes(localPhase)
        ? localPhase
        : serverPhase === 'failed'
            ? 'failed'
            : serverPhase === 'requested'
                ? 'requested'
                : localPhase;

    const stopRowClick = (event: React.MouseEvent<HTMLElement>) => {
        event.stopPropagation();
    };

    const requestConfirmation = (event: React.MouseEvent<HTMLButtonElement>) => {
        stopRowClick(event);
        if (['requested', 'submitting', 'unknown'].includes(effectivePhase)) return;
        setConfirming(true);
    };

    const cancelConfirmation = (event: React.MouseEvent<HTMLButtonElement>) => {
        stopRowClick(event);
        setConfirming(false);
    };

    const terminate = async (event: React.MouseEvent<HTMLButtonElement>) => {
        stopRowClick(event);
        const owner = selection.current;
        const isCurrent = () => Boolean(owner?.active && session === api.getAuthGeneration());
        if (!isCurrent() || requestInFlight.current || ['requested', 'unknown'].includes(effectivePhase)) return;

        requestInFlight.current = true;
        setConfirming(false);
        setLocalError(null);
        setLocalPhase('submitting');

        try {
            const response = await api.terminateCall(call.id);
            if (!isCurrent()) return;
            if (response.error) {
                const details = terminationDetails(response.error.details, call.id);
                if (details?.termination_status === 'requested' && details.provider_hangup_confirmed === false) {
                    setLocalPhase('requested');
                    setLocalError(null);
                    onPatch(call.id, {
                        termination_status: 'requested',
                        termination_requested_at: new Date().toISOString(),
                        provider_hangup_requested: details.provider_hangup_requested === true,
                        provider_hangup_confirmed: false,
                        termination_error: null,
                    });
                    return;
                }
                // Only an explicit rejection before dispatch permits another
                // confirmed attempt. A timeout may follow a committed request.
                const rejected = (details?.termination_status === 'failed' && details.provider_hangup_requested === false)
                    || [400, 401, 403, 422].includes(response.error.status ?? 0);
                if (rejected) {
                    setLocalPhase('failed');
                    setLocalError(response.error.message);
                    return;
                }
                throw new Error(response.error.message);
            }
            if (!response.data || response.data.call_id !== call.id) throw new Error('The termination result could not be verified.');

            const classification = classifyTerminationResult(response.data);
            if (classification.phase === 'confirmed') {
                if (response.data.termination_status !== 'confirmed' || response.data.provider_hangup_confirmed !== true) {
                    throw new Error('Provider confirmation is unavailable.');
                }
                if (response.data.termination_receipt_saved === false) {
                    const gap = response.data.termination_reconciliation_error || 'Provider hangup confirmed; the durable confirmation could not be saved.';
                    setLocalPhase('confirmed');
                    setLocalError(gap);
                    onPatch(call.id, { termination_status: 'confirmed', provider_hangup_confirmed: true, termination_error: gap });
                    return;
                }
                onConfirmed(call.id);
                return;
            }
            if (classification.phase === 'failed' && response.data.provider_hangup_requested !== false) {
                throw new Error(classification.error || 'The termination outcome is unknown.');
            }

            const patch = terminationPatchFromResult(response.data);
            onPatch(call.id, patch);
            setLocalPhase(classification.phase);
            setLocalError(classification.error);
        } catch (error) {
            if (!isCurrent()) return;
            const message = error instanceof Error
                ? error.message
                : 'Failed to request call termination.';
            setLocalPhase('unknown');
            setLocalError(`${message} The request may have been applied. Check its status before another action.`);
        } finally {
            if (isCurrent()) requestInFlight.current = false;
        }
    };

    const checkStatus = async (event: React.MouseEvent<HTMLButtonElement>) => {
        stopRowClick(event);
        const owner = selection.current;
        const isCurrent = () => Boolean(owner?.active && session === api.getAuthGeneration());
        if (!isCurrent() || requestInFlight.current) return;
        requestInFlight.current = true;
        setChecking(true);
        try {
            const response = await api.getAdminCallDetail(call.id);
            if (!isCurrent()) return;
            if (response.error || response.data?.id !== call.id) {
                throw new Error(response.error?.message || 'The selected call status is unavailable.');
            }
            const current = response.data;
            if (current.termination_status === 'confirmed' && current.provider_hangup_confirmed === true) {
                onConfirmed(call.id);
            } else if (current.status === 'termination_pending' || current.termination_status === 'requested') {
                setLocalPhase('requested');
                setLocalError(null);
                onPatch(call.id, { termination_status: 'requested', termination_error: null, provider_hangup_confirmed: false });
            } else {
                // A terminal DB label alone does not prove all provider legs
                // are absent. This read must never issue a second hangup.
                setLocalError(`Latest recorded call status: ${current.status}. ${current.termination_reconciliation_error || 'Provider confirmation is still unavailable; review the call before another action.'}`);
            }
        } catch (error) {
            if (isCurrent()) setLocalError(error instanceof Error ? error.message : 'Could not check call status.');
        } finally {
            if (isCurrent()) { setChecking(false); requestInFlight.current = false; }
        }
    };

    if (serverPhase === 'confirmed' || localPhase === 'confirmed') {
        return (
            <div className="termination-action" onClick={stopRowClick}>
                <button
                    className="btn btn-secondary btn-sm"
                    type="button"
                    disabled
                    aria-label="Call termination confirmed"
                >
                    <PhoneOff size={14} />
                    Ended
                </button>
                {(localError || call.termination_error) && <span className="termination-error" role="alert">{localError || call.termination_error}</span>}
            </div>
        );
    }

    if (effectivePhase === 'requested' || effectivePhase === 'submitting' || effectivePhase === 'unknown') {
        return (
            <div className="termination-action" onClick={stopRowClick}>
                <button
                    className="btn btn-secondary btn-sm termination-pending-button"
                    type="button"
                    disabled
                    aria-label={effectivePhase === 'unknown' ? 'Call termination outcome unknown' : 'Call termination is awaiting provider confirmation'}
                >
                    <Loader2 className="animate-spin" size={14} />
                    {effectivePhase === 'unknown' ? 'Outcome unknown' : 'Ending…'}
                </button>
                <span className="termination-note" role="status" aria-live="polite">
                    {effectivePhase === 'unknown' ? 'Check the existing request; do not resend it.' : 'Awaiting provider confirmation'}
                </span>
                {effectivePhase !== 'submitting' && <button type="button" className="btn btn-secondary btn-sm" disabled={checking} onClick={(event) => void checkStatus(event)}>{checking ? 'Checking…' : 'Check status'}</button>}
                {localError && <span className="termination-error" role="alert">{localError}</span>}
            </div>
        );
    }

    if (confirming) {
        return (
            <div className="confirm-inline call-termination-confirm" onClick={stopRowClick}>
                <span>{effectivePhase === 'failed' ? 'Retry ending?' : 'End this call?'}</span>
                <button
                    className="btn btn-danger btn-sm"
                    type="button"
                    onClick={terminate}
                >
                    Yes
                </button>
                <button
                    className="btn btn-secondary btn-sm"
                    type="button"
                    onClick={cancelConfirmation}
                >
                    No
                </button>
            </div>
        );
    }

    const failed = effectivePhase === 'failed';
    const error = localError || call.termination_error;

    return (
        <div className="termination-action" onClick={stopRowClick}>
            <button
                className="btn btn-danger btn-sm"
                type="button"
                onClick={requestConfirmation}
            >
                {failed ? <RotateCcw size={14} /> : <PhoneOff size={14} />}
                {failed ? 'Retry end' : 'End'}
            </button>
            {failed && (
                <span className="termination-error" role="alert">
                    {error || 'The hangup was not confirmed. Retry is available.'}
                </span>
            )}
        </div>
    );
}
