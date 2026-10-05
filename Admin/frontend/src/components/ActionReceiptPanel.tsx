import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { ActionDetail, CalendarInspection, EmailInspection } from '../lib/api';

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

export function ActionReceiptPanel({ action }: { action: ActionDetail }) {
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
