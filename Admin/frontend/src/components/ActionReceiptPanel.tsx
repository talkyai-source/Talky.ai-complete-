import { useEffect, useRef, useState } from 'react';
import { api, isAcknowledgementRecoveryCapability, isAcknowledgementRecoveryRecord, isRecoveryReason, isRecoveryUuid } from '../lib/api';
import type { ActionDetail, AcknowledgementRecoveryRequest, CalendarInspection, EmailInspection } from '../lib/api';

type PendingRecovery = { request: AcknowledgementRecoveryRequest; providerStatus: string; reloadRequired: boolean };
// Memory only, bounded, and scoped to the current authentication generation.
// Uncertain requests are never evicted to make room for a new request.
const pendingRecoveries = new Map<string, PendingRecovery>();
let recoverySession = -1;
function pendingRecovery(actionId: string): PendingRecovery | undefined {
    if (recoverySession !== api.getAuthGeneration()) {
        pendingRecoveries.clear();
        recoverySession = api.getAuthGeneration();
    }
    return pendingRecoveries.get(actionId);
}

function resolvesPending(action: ActionDetail, pending: PendingRecovery): boolean {
    const record = action.acknowledgement_recovery_record;
    return action.status === 'completed' && action.saved_receipt?.action_id === action.id
        && action.saved_receipt.status === 'completed' && action.saved_receipt.success === true
        && action.saved_receipt.confirmation_allowed === true && isAcknowledgementRecoveryRecord(record, action.id)
        && record.source_digest === pending.request.expected_source_digest && record.provider_status === pending.providerStatus;
}

function confirmsPending(action: ActionDetail, pending: PendingRecovery): boolean {
    return resolvesPending(action, pending) && action.acknowledgement_recovery_record?.request_id === pending.request.request_id
        && action.acknowledgement_recovery_record?.reason === pending.request.reason;
}

const referenceLabels = {
    identity_version: 'Saved identity proof',
    account_row_id: 'Original authorization row',
    provider: 'Recorded provider',
    connector_id: 'Original connector ID',
    external_account_id: 'Original account ID',
    message_id: 'Message reference',
    external_event_id: 'Calendar event reference',
    meeting_id: 'Meeting reference',
    job_id: 'Job reference',
    reminder_id: 'Reminder reference',
    plan_id: 'Plan reference',
    child_action_id: 'Child action reference',
    provider_status: 'Recorded provider status',
} as const;

const observationLabels = {
    observed_message: 'Exact message observed',
    not_observed: 'Message not observed — inconclusive',
    unavailable: 'Inspection unavailable',
};
const observationReasons = {
    exact_message_observed_only: 'The saved message ID was returned in the original authorization. This does not prove it was sent, delivered, or contained the intended payload.',
    absence_is_inconclusive: 'The provider did not return this message. This does not prove non-execution or make another send safe.',
    saved_proof_unavailable: 'A complete, consistent original authorization and message reference is unavailable.',
    original_authorization_unavailable: 'The original active authorization with a usable unexpired token is unavailable.',
    provider_read_unavailable: 'The provider observation could not be obtained. The saved receipt is unchanged.',
};
const calendarObservationLabels = {
    observed_event: 'Exact event reference observed',
    not_observed: 'Event not observed — inconclusive',
    unavailable: 'Inspection unavailable',
};
const calendarObservationReasons = {
    ...observationReasons,
    exact_event_observed_only: 'The saved event ID was returned in the original authorization. It may be a cancelled or deleted reference. This does not prove an active booking or that creation, update, or cancellation succeeded.',
    absence_is_inconclusive: 'The provider did not return this event. This does not prove non-execution or make another calendar action safe.',
    saved_proof_unavailable: 'A complete, consistent original authorization and event reference is unavailable.',
};

export function ActionReceiptPanel({ action, onReloadReceipt }: {
    action: ActionDetail;
    onReloadReceipt?: () => Promise<ActionDetail | null>;
}) {
    const saved = action.saved_receipt;
    const matches = saved?.action_id === action.id && saved.status === action.status;
    const refs = matches ? saved.receipt : undefined;
    const confirmed = matches && saved.success === true && saved.confirmation_allowed === true
        && ['completed', 'scheduled'].includes(action.status);
    const calendar = action.calendar_inspection_available === true && action.email_inspection_available !== true;
    const canInspect = matches && (calendar || (action.email_inspection_available === true && action.calendar_inspection_available !== true));
    const labels = calendar ? calendarObservationLabels : observationLabels;
    const reasons = calendar ? calendarObservationReasons : observationReasons;
    const selection = JSON.stringify([action.id, action.type, action.status, canInspect, calendar, refs?.identity_version, refs?.tenant_id,
        refs?.provider, refs?.connector_id, refs?.account_row_id, refs?.external_account_id, refs?.message_id, refs?.external_event_id]);
    const [observation, setObservation] = useState<{ selection: string; value: EmailInspection | CalendarInspection } | null>(null);
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const generation = useRef(0);
    const inFlight = useRef(false);
    const session = api.getAuthGeneration();
    const [reason, setReason] = useState('');
    const [confirmRecovery, setConfirmRecovery] = useState(false);
    const [recovering, setRecovering] = useState(false);
    const [recoveryMessage, setRecoveryMessage] = useState('');
    const recoveryFlight = useRef(false);
    const recoveryGeneration = useRef(0);
    const cachedRecovery = pendingRecovery(action.id);
    const unresolved = cachedRecovery && !resolvesPending(action, cachedRecovery) ? cachedRecovery : undefined;
    const capability = action.acknowledgement_recovery;
    const recoveryRecord = isAcknowledgementRecoveryRecord(action.acknowledgement_recovery_record, action.id)
        ? action.acknowledgement_recovery_record : null;
    const canRecover = Boolean(onReloadReceipt && matches && action.type === 'send_email' && action.status === 'unknown'
        && isRecoveryUuid(action.id) && isAcknowledgementRecoveryCapability(capability) && !action.acknowledgement_recovery_record);
    const recoverySelection = JSON.stringify([selection, session, capability?.source_digest, capability?.provider_status,
        recoveryRecord?.id]);
    useEffect(() => {
        recoveryGeneration.current += 1;
        recoveryFlight.current = false;
        setReason(''); setConfirmRecovery(false); setRecovering(false); setRecoveryMessage('');
        return () => { recoveryGeneration.current += 1; };
    }, [recoverySelection]);
    useEffect(() => {
        const existing = pendingRecovery(action.id);
        if (existing && resolvesPending(action, existing)) pendingRecoveries.delete(action.id);
    }, [action]);
    useEffect(() => {
        generation.current += 1;
        inFlight.current = false;
        setObservation(null); setPending(false); setFailed(false);
        return () => { generation.current += 1; };
    }, [selection]);

    const inspect = async () => {
        if (!canInspect || inFlight.current) return;
        const ownGeneration = generation.current;
        inFlight.current = true;
        setPending(true); setFailed(false); setObservation(null);
        try {
            const response = await (calendar ? api.inspectAdminCalendarAction(action.id) : api.inspectAdminEmailAction(action.id));
            if (generation.current !== ownGeneration) return;
            const result = response.data;
            const observedId = result && (calendar
                ? ('observed_event_id' in result ? result.observed_event_id : undefined)
                : ('observed_message_id' in result ? result.observed_message_id : undefined));
            const valid = result && !response.error && result.action_id === action.id
                && typeof result.observed_at === 'string' && result.observed_at.length <= 64
                && Number.isFinite(Date.parse(result.observed_at))
                && Object.hasOwn(labels, result.outcome) && Object.hasOwn(reasons, result.reason)
                && (result.outcome === (calendar ? 'observed_event' : 'observed_message')
                    ? result.reason === (calendar ? 'exact_event_observed_only' : 'exact_message_observed_only')
                        && typeof observedId === 'string' && observedId.length > 0
                        && observedId === (calendar ? refs?.external_event_id : refs?.message_id)
                    : observedId === null && (result.outcome === 'not_observed'
                        ? result.reason === 'absence_is_inconclusive'
                        : ['saved_proof_unavailable', 'original_authorization_unavailable', 'provider_read_unavailable'].includes(result.reason)));
            if (valid) setObservation({ selection, value: result });
            else setFailed(true);
        } catch {
            if (generation.current === ownGeneration) setFailed(true);
        } finally {
            if (generation.current === ownGeneration) { inFlight.current = false; setPending(false); }
        }
    };
    const currentObservation = observation?.selection === selection ? observation.value : null;
    const currentId = currentObservation && (calendar
        ? ('observed_event_id' in currentObservation ? currentObservation.observed_event_id : null)
        : ('observed_message_id' in currentObservation ? currentObservation.observed_message_id : null));

    const reloadReceipt = async () => {
        if (!onReloadReceipt || recoveryFlight.current || session !== api.getAuthGeneration()) return;
        const ownGeneration = recoveryGeneration.current;
        const ownSession = session;
        const active = () => ownSession === api.getAuthGeneration() && ownGeneration === recoveryGeneration.current;
        recoveryFlight.current = true; setRecovering(true);
        try {
            const refreshed = await onReloadReceipt();
            if (!active()) return;
            const existing = pendingRecovery(action.id);
            if (!refreshed || refreshed.id !== action.id) {
                setRecoveryMessage('Receipt reload failed. The previous request is preserved.');
            } else if (existing && resolvesPending(refreshed, existing)) {
                pendingRecoveries.delete(action.id);
                setRecoveryMessage(confirmsPending(refreshed, existing) ? 'Saved acknowledgement recovery recorded.'
                    : 'The saved receipt records recovery under another review.');
            } else {
                if (existing) existing.reloadRequired = !(refreshed.status === 'unknown'
                    && isAcknowledgementRecoveryCapability(refreshed.acknowledgement_recovery)
                    && refreshed.acknowledgement_recovery.source_digest === existing.request.expected_source_digest
                    && refreshed.acknowledgement_recovery.provider_status === existing.providerStatus);
                setRecoveryMessage(existing?.reloadRequired
                    ? 'The saved evidence or authority has changed. The previous request remains held.'
                    : 'Receipt reloaded. Any retry uses the same recorded review request.');
            }
        } catch {
            if (active()) setRecoveryMessage('Receipt reload failed. The previous request is preserved.');
        } finally {
            if (active()) { recoveryFlight.current = false; setRecovering(false); }
        }
    };

    const recover = async () => {
        if (!canRecover || recoveryFlight.current || api.getAuthGeneration() !== session) return;
        let request = pendingRecovery(action.id);
        if (request?.reloadRequired) return;
        if (!request) {
            if (!confirmRecovery || !isRecoveryReason(reason.trim()) || !isAcknowledgementRecoveryCapability(capability)) return;
            if (pendingRecoveries.size >= 32) {
                setRecoveryMessage('Resolve an existing pending recovery before starting another.'); return;
            }
            request = { request: { request_id: crypto.randomUUID(), expected_source_digest: capability.source_digest, reason: reason.trim() },
                providerStatus: capability.provider_status, reloadRequired: false };
            pendingRecoveries.set(action.id, request);
        } else if (!isAcknowledgementRecoveryCapability(capability)
            || request.request.expected_source_digest !== capability.source_digest || request.providerStatus !== capability.provider_status) {
            request.reloadRequired = true; setRecoveryMessage('Saved evidence changed. Reload the receipt before another request.'); return;
        }
        const ownGeneration = recoveryGeneration.current;
        const active = () => session === api.getAuthGeneration() && ownGeneration === recoveryGeneration.current;
        recoveryFlight.current = true; setRecovering(true); setConfirmRecovery(false); setRecoveryMessage('');
        try {
            const response = await api.recoverSavedAcknowledgement(action.id, request.request);
            if (!active()) return;
            if (response.error) {
                const status = response.error.status;
                request.reloadRequired = status === 401 || status === 403 || status === 409 || status === 404 || status === 422;
                setRecoveryMessage(status === 401 || status === 403
                    ? 'Current platform admin authority or session is unavailable. Reload the receipt after signing in.'
                    : request.reloadRequired ? 'Recovery was not confirmed. Reload the receipt before another request.'
                        : 'Recovery is unconfirmed. Reload the receipt or retry the same request.');
                return;
            }
            const record = response.data;
            if (!isAcknowledgementRecoveryRecord(record, action.id) || record.request_id !== request.request.request_id
                || record.source_digest !== request.request.expected_source_digest || record.reason !== request.request.reason
                || record.provider_status !== request.providerStatus) {
                setRecoveryMessage('Recovery is unconfirmed. Reload the receipt or retry the same request.'); return;
            }
            const refreshed = await onReloadReceipt!();
            if (!active()) return;
            if (refreshed?.id === action.id && resolvesPending(refreshed, request)) {
                pendingRecoveries.delete(action.id);
                setRecoveryMessage(confirmsPending(refreshed, request) ? 'Saved acknowledgement recovery recorded.'
                    : 'The saved receipt records recovery under another review.');
            } else {
                setRecoveryMessage('Recovery response received; the refreshed receipt is unconfirmed. Reload the receipt or retry the same request.');
            }
        } catch {
            if (active()) setRecoveryMessage('Recovery is unconfirmed. Reload the receipt or retry the same request.');
        } finally {
            if (active()) { recoveryFlight.current = false; setRecovering(false); }
        }
    };
    return <section className="action-audit" aria-label="Saved action receipt">
        <h4>Saved action receipt</h4>
        <p><strong>{confirmed ? 'Acknowledgement recorded' : 'Outcome unverified'}</strong></p>
        <p>Saved evidence only. This view does not verify current provider state or recipient delivery.</p>
        {!confirmed && <p>Keep uncertain actions held. Review the original account and receipt before another action; no automatic retry is available.</p>}
        {!matches && <p>The saved receipt is unavailable.</p>}
        <div className="audit-row"><span className="label">Action ID</span><span className="value mono">{action.id}</span></div>
        {Object.entries(referenceLabels).map(([key, label]) => {
            const value = refs?.[key as keyof typeof referenceLabels];
            const valid = typeof value === 'string' && value.trim() && value.length <= 512;
            if (!valid && !['provider', 'connector_id', 'external_account_id'].includes(key)) return null;
            return <div className="audit-row" key={key}>
                <span className="label">{label}</span><span className="value mono">{valid ? value : 'Unavailable'}</span>
            </div>;
        })}
        {recoveryRecord && confirmed && action.status === 'completed' && <div aria-label="Saved acknowledgement recovery">
            <h4>Saved acknowledgement recovered</h4>
            <p>This records provider acceptance only. It does not establish delivery or payload correctness.</p>
            <p>Reviewed by platform admin: {recoveryRecord.actor_id}</p>
            <p>Recorded at: {recoveryRecord.recorded_at}</p>
            <p>Reason: {recoveryRecord.reason}</p>
            <p>Status: {recoveryRecord.original_status} → {recoveryRecord.recovered_status}</p>
            <p>Recorded provider status: {recoveryRecord.provider_status}</p>
        </div>}
        {onReloadReceipt && (canRecover || unresolved) && <div aria-label="Recover saved acknowledgement">
            <p>Restore the provider acceptance already saved for this action. This does not send another email or establish delivery or payload correctness.</p>
            {unresolved ? <>
                <p>A review request is pending. Its reason and request ID are preserved until the saved receipt confirms recovery.</p>
                <p>Review reason: {unresolved.request.reason}</p>
                <button type="button" className="btn btn-secondary btn-sm" disabled={recovering || unresolved.reloadRequired || !canRecover}
                    onClick={() => void recover()}>Retry same recovery request</button>
            </> : <>
                <label>Recovery reason<textarea aria-label="Recovery reason" maxLength={500} value={reason} disabled={recovering}
                    onChange={(event) => { setReason(event.target.value); setConfirmRecovery(false); }} /></label>
                {confirmRecovery ? <>
                    <p>Confirm restoring this saved acknowledgement with the reason above?</p>
                    <button type="button" className="btn btn-primary btn-sm" disabled={recovering}
                        onClick={() => void recover()}>Confirm saved acknowledgement recovery</button>
                    <button type="button" className="btn btn-secondary btn-sm" disabled={recovering}
                        onClick={() => setConfirmRecovery(false)}>Keep held</button>
                </> : <button type="button" className="btn btn-secondary btn-sm" disabled={!isRecoveryReason(reason.trim()) || recovering}
                    onClick={() => setConfirmRecovery(true)}>Recover saved acknowledgement</button>}
            </>}
            <button type="button" className="btn btn-secondary btn-sm" disabled={recovering} onClick={() => void reloadReceipt()}>Reload saved receipt</button>
            <div aria-live="polite">{recovering ? <p>Checking saved acknowledgement…</p> : recoveryMessage && <p>{recoveryMessage}</p>}</div>
        </div>}
        {canInspect && <div>
            <button type="button" className="btn btn-secondary btn-sm" disabled={pending} onClick={() => void inspect()}>
                {pending ? 'Inspecting original authorization…' : calendar ? 'Inspect saved calendar event' : 'Inspect saved Gmail message'}
            </button>
            <p>Read-only observation. This does not change or retry the saved action.</p>
            <div aria-live="polite">
                {failed && <p>Inspection unavailable. Saved receipt unchanged.</p>}
                {currentObservation && <>
                    <p><strong>{labels[currentObservation.outcome as keyof typeof labels]}</strong></p>
                    <p>{reasons[currentObservation.reason as keyof typeof reasons]}</p>
                    {currentId && <p>{calendar ? 'Observed event ID: ' : 'Observed message ID: '}{currentId}</p>}
                    <p>Observed at: {currentObservation.observed_at}</p>
                </>}
            </div>
        </div>}
    </section>;
}
