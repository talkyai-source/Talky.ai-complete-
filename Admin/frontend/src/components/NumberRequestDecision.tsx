import { useRef, useState } from 'react';
import { ConfirmationModal } from './ConfirmationModal';
import { api } from '../lib/api';
import {
    INCOMPLETE_ANSWER, NUMBER_REQUEST_NOTE_MAX, decisionResultText, isAlreadyDecided, isDecisionUnconfirmed,
    validNumberRequestDecision, type NumberRequestDecision as Decision, type NumberRequestDecisionInput,
} from '../lib/number-request-contracts';
import './NumberRequests.css';

/**
 * Approve and Reject for a submitted request. One POST per confirmation; nothing is
 * sent twice while busy, and after an answer that leaves the outcome decided or
 * unknown (409, 503 decision_unconfirmed, an unreadable answer) nothing is sent
 * again until the request has been read again.
 */
export function NumberRequestDecision({ tenantId, requestId, disabled, owns, onDecided, onReload }: {
    tenantId: string; requestId: string; disabled: boolean; owns: () => boolean;
    onDecided: (result: string) => void; onReload: () => void;
}) {
    const [open, setOpen] = useState<Decision | null>(null);
    const [reason, setReason] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [needsReload, setNeedsReload] = useState(false);
    const sending = useRef(false);
    const actions = useRef<HTMLDivElement>(null);

    const show = (decision: Decision) => { setError(null); setOpen(decision); };
    const close = () => { if (!busy) setOpen(null); };

    async function send(decision: Decision) {
        const note = reason.trim().slice(0, NUMBER_REQUEST_NOTE_MAX);
        if (sending.current || needsReload || (decision === 'rejected' && !note)) return;
        sending.current = true;
        setBusy(true);
        setError(null);
        const body: NumberRequestDecisionInput = decision === 'approved' ? { decision } : { decision, note };
        try {
            const response = await api.decideNumberRequest(tenantId, requestId, body);
            if (!owns()) return;
            if (response.error) {
                if (isAlreadyDecided(response.error) || isDecisionUnconfirmed(response.error)) setNeedsReload(true);
                setError(response.error.message);
                return;
            }
            if (!validNumberRequestDecision(response.data)) {
                setNeedsReload(true);
                setError(INCOMPLETE_ANSWER);
                return;
            }
            setOpen(null);
            onDecided(decisionResultText(decision, response.data.email));
        } catch {
            if (owns()) { setNeedsReload(true); setError(INCOMPLETE_ANSWER); }
        } finally {
            sending.current = false;
            if (owns()) setBusy(false);
        }
    }

    return (
        <div className="number-request-decision">
            {needsReload && error && !open && (
                <div className="error-banner number-request-banner" role="alert">
                    <p>{error}</p>
                    <button type="button" className="btn btn-secondary btn-sm" onClick={onReload}>Reload</button>
                </div>
            )}
            <div className="number-request-actions" ref={actions} tabIndex={-1}>
                <button type="button" className="btn btn-primary" disabled={disabled || busy || needsReload} onClick={() => show('approved')}>Approve</button>
                <button type="button" className="btn btn-secondary" disabled={disabled || busy || needsReload} onClick={() => show('rejected')}>Reject</button>
            </div>
            <ConfirmationModal isOpen={open === 'approved'} variant="info" title="Approve this request?"
                message="The customer will be emailed that their phone number request was approved. This cannot be undone."
                confirmLabel="Approve" loading={busy} error={error} confirmDisabled={needsReload}
                onConfirm={() => void send('approved')} onCancel={close} returnFocusFallback={actions} />
            <ConfirmationModal isOpen={open === 'rejected'} variant="danger" title="Reject this request?"
                message="The customer will be emailed that their phone number request was not approved. This cannot be undone."
                confirmLabel="Reject" loading={busy} error={error} confirmDisabled={needsReload}
                reason={reason} onReasonChange={(value) => setReason(value.slice(0, NUMBER_REQUEST_NOTE_MAX))}
                reasonLabel="Reason" reasonPlaceholder="" reasonHelp="The customer is emailed this reason." reasonAnyNonBlank
                onConfirm={() => void send('rejected')} onCancel={close} returnFocusFallback={actions} />
        </div>
    );
}
