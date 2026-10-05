import type { ActionDetail } from '../lib/api';

const referenceLabels = {
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

export function ActionReceiptPanel({ action }: { action: ActionDetail }) {
    const saved = action.saved_receipt;
    const matches = saved?.action_id === action.id && saved.status === action.status;
    const refs = matches ? saved.receipt : undefined;
    const confirmed = matches && saved.success === true && saved.confirmation_allowed === true
        && ['completed', 'scheduled'].includes(action.status);
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
    </section>;
}
