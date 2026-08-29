import type {
    CallTerminationStatus,
    LiveCallItem,
    TerminateCallResponse,
} from './api.ts';

export type TerminationUiPhase = 'idle' | 'requested' | 'confirmed' | 'failed';

export interface TerminationResultClassification {
    phase: Exclude<TerminationUiPhase, 'idle'>;
    error: string | null;
}

const TERMINAL_CALL_STATUSES = new Set([
    'ended',
    'completed',
    'failed',
    'cancelled',
    'canceled',
    'busy',
    'no_answer',
    'rejected',
]);

export function isTerminalCallStatus(status: string | null | undefined): boolean {
    return TERMINAL_CALL_STATUSES.has((status || '').trim().toLowerCase());
}

export function getTerminationPhase(
    call: Pick<LiveCallItem, 'status' | 'termination_status'>,
): TerminationUiPhase {
    // A terminal database label is not PBX absence proof. Only the explicit
    // confirmation field may project provider-confirmed termination.
    if (call.termination_status === 'confirmed') return 'confirmed';
    if (call.termination_status === 'requested') return 'requested';
    if (call.termination_status === 'failed') return 'failed';
    return 'idle';
}

/**
 * Interpret the termination response conservatively. In particular, a
 * `requested` result is never upgraded to success because a status-looking
 * field happens to contain `ended`.
 */
export function classifyTerminationResult(
    result: TerminateCallResponse,
): TerminationResultClassification {
    if (result.status === 'already_terminal') {
        return { phase: 'confirmed', error: null };
    }

    if (result.status === 'requested' || result.termination_status === 'requested') {
        return { phase: 'requested', error: null };
    }

    if (
        result.status === 'confirmed'
        && result.termination_status === 'confirmed'
        && result.provider_hangup_confirmed
    ) {
        return { phase: 'confirmed', error: null };
    }

    const error = result.provider_hangup_error?.trim()
        || 'The provider did not confirm the hangup. Retry ending the call.';
    return { phase: 'failed', error };
}

export function terminationPatchFromResult(
    result: TerminateCallResponse,
    now = new Date(),
): Partial<LiveCallItem> {
    const classification = classifyTerminationResult(result);
    const status: CallTerminationStatus = classification.phase;

    return {
        termination_status: status,
        termination_requested_at: status === 'requested' ? now.toISOString() : null,
        termination_error: classification.error,
        provider_hangup_requested: result.provider_hangup_requested,
        provider_hangup_confirmed: result.provider_hangup_confirmed,
    };
}

/**
 * A poll can race the write that persisted `termination_status=requested`.
 * Preserve that local pending marker until the server reports a newer
 * requested/failed/confirmed state or the call disappears from the live list.
 */
export function mergePolledLiveCalls(
    previous: LiveCallItem[],
    incoming: LiveCallItem[],
): LiveCallItem[] {
    const previousById = new Map(previous.map((call) => [call.id, call]));

    return incoming.map((call) => {
        const oldCall = previousById.get(call.id);
        if (
            oldCall?.termination_status === 'requested'
            && (!call.termination_status || call.termination_status === 'none')
        ) {
            return {
                ...call,
                termination_status: 'requested',
                termination_requested_at: oldCall.termination_requested_at,
                termination_error: null,
                provider_hangup_requested: oldCall.provider_hangup_requested,
                provider_hangup_confirmed: false,
            };
        }
        return call;
    });
}
