import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { ActionDetail, EmailInspection } from '../lib/api';

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

export function ActionReceiptPanel({ action }: { action: ActionDetail }) {
    const saved = action.saved_receipt;
    const matches = saved?.action_id === action.id && saved.status === action.status;
    const refs = matches ? saved.receipt : undefined;
    const confirmed = matches && saved.success === true && saved.confirmation_allowed === true
        && ['completed', 'scheduled'].includes(action.status);
    const canInspect = matches && action.email_inspection_available === true;
    const selection = JSON.stringify([action.id, action.status, canInspect, refs?.identity_version, refs?.tenant_id,
        refs?.provider, refs?.connector_id, refs?.account_row_id, refs?.external_account_id, refs?.message_id]);
    const [observation, setObservation] = useState<{ selection: string; value: EmailInspection } | null>(null);
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
            const response = await api.inspectAdminEmailAction(action.id);
            if (generation.current !== ownGeneration) return;
            const result = response.data;
            const valid = result && !response.error && result.action_id === action.id
                && Object.hasOwn(observationLabels, result.outcome) && Object.hasOwn(observationReasons, result.reason)
                && (result.outcome === 'observed_message'
                    ? result.reason === 'exact_message_observed_only' && result.observed_message_id === refs?.message_id
                    : result.observed_message_id === null && (result.outcome === 'not_observed'
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
                {pending ? 'Inspecting original authorization…' : 'Inspect saved Gmail message'}
            </button>
            <p>Read-only observation. This does not change or retry the saved action.</p>
            <div aria-live="polite">
                {failed && <p>Inspection unavailable. Saved receipt unchanged.</p>}
                {currentObservation && <>
                    <p><strong>{observationLabels[currentObservation.outcome]}</strong></p>
                    <p>{observationReasons[currentObservation.reason]}</p>
                    {currentObservation.observed_message_id && <p>Observed message ID: {currentObservation.observed_message_id}</p>}
                    <p>Observed at: {currentObservation.observed_at}</p>
                </>}
            </div>
        </div>}
    </section>;
}
