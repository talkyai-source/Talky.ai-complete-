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

type LocalPhase = 'idle' | 'submitting' | 'requested' | 'failed';

export function CallTerminationAction({
    call,
    onPatch,
    onConfirmed,
}: CallTerminationActionProps) {
    const serverPhase = getTerminationPhase(call);
    const [localPhase, setLocalPhase] = useState<LocalPhase>('idle');
    const [localError, setLocalError] = useState<string | null>(null);
    const [confirming, setConfirming] = useState(false);
    const requestInFlight = useRef(false);

    useEffect(() => {
        if (serverPhase === 'requested') {
            setLocalPhase('requested');
            setLocalError(null);
            setConfirming(false);
        } else if (serverPhase === 'failed') {
            setLocalPhase('failed');
            setLocalError(call.termination_error || 'The hangup was not confirmed.');
            setConfirming(false);
        }
    }, [call.termination_error, serverPhase]);

    const effectivePhase: LocalPhase = localPhase === 'submitting'
        ? 'submitting'
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
        if (effectivePhase === 'requested' || effectivePhase === 'submitting') return;
        setConfirming(true);
    };

    const cancelConfirmation = (event: React.MouseEvent<HTMLButtonElement>) => {
        stopRowClick(event);
        setConfirming(false);
    };

    const terminate = async (event: React.MouseEvent<HTMLButtonElement>) => {
        stopRowClick(event);
        if (requestInFlight.current || effectivePhase === 'requested') return;

        requestInFlight.current = true;
        setConfirming(false);
        setLocalError(null);
        setLocalPhase('submitting');

        try {
            const response = await api.terminateCall(call.id);
            if (response.error) throw new Error(response.error.message);
            if (!response.data) throw new Error('The termination request returned no result.');

            const classification = classifyTerminationResult(response.data);
            if (classification.phase === 'confirmed') {
                onConfirmed(call.id);
                return;
            }

            const patch = terminationPatchFromResult(response.data);
            onPatch(call.id, patch);
            setLocalPhase(classification.phase);
            setLocalError(classification.error);
        } catch (error) {
            const message = error instanceof Error
                ? error.message
                : 'Failed to request call termination.';
            setLocalPhase('failed');
            setLocalError(message);
            onPatch(call.id, {
                termination_status: 'failed',
                termination_error: message,
                provider_hangup_confirmed: false,
            });
        } finally {
            requestInFlight.current = false;
        }
    };

    if (serverPhase === 'confirmed') {
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
            </div>
        );
    }

    if (effectivePhase === 'requested' || effectivePhase === 'submitting') {
        return (
            <div className="termination-action" onClick={stopRowClick}>
                <button
                    className="btn btn-secondary btn-sm termination-pending-button"
                    type="button"
                    disabled
                    aria-label="Call termination is awaiting provider confirmation"
                >
                    <Loader2 className="animate-spin" size={14} />
                    Ending…
                </button>
                <span className="termination-note" role="status" aria-live="polite">
                    Awaiting provider confirmation
                </span>
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
