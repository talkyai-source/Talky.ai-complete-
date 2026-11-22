import { useRef, useState } from 'react';
import { api } from '../lib/api';
import { ConfirmationModal } from './ConfirmationModal';
import { useAdminRead } from '../lib/use-admin-read';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import { accountText, amountText, kindText, paymentReviewPage, waitingText } from '../lib/payment-reviews';
import type { PaymentReview, PaymentReviewPage } from '../lib/payment-reviews';

const MIN_NOTE = 12;
const UNCONFIRMED = 'The result is not confirmed. Read the list again before trying again.';
const loadReviews = () => api.getPaymentReviews();
const validPage = (value: unknown) => paymentReviewPage(value) !== null;

export function PaymentReviewsCard() {
    const generation = useAuthGeneration();
    const read = useAdminRead<unknown>(loadReviews, 60000, validPage);
    const page: PaymentReviewPage | null = read.data === null || read.data === undefined ? null : paymentReviewPage(read.data);
    const [selected, setSelected] = useState<PaymentReview | null>(null);
    const [note, setNote] = useState('');
    const [checked, setChecked] = useState(false);
    const [confirming, setConfirming] = useState(false);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState('');
    const sending = useRef(false);
    const capture = useOperationOwner(generation, 'payment-reviews');

    const choose = (row: PaymentReview) => {
        if (busy) return;
        setSelected(row); setNote(''); setChecked(false); setMessage('');
    };

    const close = async () => {
        const owns = capture();
        const row = selected;
        if (!owns() || !row || sending.current) return;
        sending.current = true; setBusy(true); setMessage('');
        try {
            const result = await api.closePaymentReview({ provider: row.provider, event_id: row.event_id,
                seen_attempt_count: row.attempt_count, reason: note.trim() });
            if (!owns()) return;
            const data = result.data as { changed?: unknown } | undefined;
            if (result.error) setMessage(result.error.status === 409 ? result.error.message : UNCONFIRMED);
            else if (data && typeof data === 'object' && typeof data.changed === 'boolean') {
                setMessage(data.changed ? 'Closed. Your name and note are recorded.' : 'This review was already closed.');
            } else setMessage(UNCONFIRMED);
        } catch {
            if (owns()) setMessage(UNCONFIRMED);
        } finally {
            sending.current = false;
            if (owns()) {
                setBusy(false); setSelected(null); setNote(''); setChecked(false);
                await read.refresh();
            }
        }
    };

    return <section className="card" aria-label="Payments that need a person">
        <div className="card-header"><h2 className="card-title">Payments that need a person</h2></div>
        <div className="card-body">
            <p>A payment needs review when Stripe or PayPal told us about money for an account but the details did not match our records, so nothing was given or taken back automatically and a person has to check it. This list is what sets off the payments alert.</p>
            {message && <p role="status" data-testid="payment-reviews-message">{message}</p>}
            {read.error && <div className="error-banner inline" role="alert" data-testid="payment-reviews-error">
                <p>The list of payments that need review could not be read. This does not mean there are none.</p>
                <button className="btn btn-secondary" disabled={busy} onClick={() => void read.refresh()}>Retry reading the list</button></div>}
            {!read.error && !page && <p role="status">Reading payments that need review…</p>}
            {!read.error && page && <>
                {page.truncated && <div className="info-banner" role="status" data-testid="payment-reviews-truncated">
                    Only the oldest {page.limit} for each provider are shown. Close some to see the rest.</div>}
                {page.items.length === 0
                    ? <p data-testid="payment-reviews-empty">Nothing needs review: every Stripe and PayPal payment message was handled.</p>
                    : <div className="table-container"><table className="data-table" aria-label="Payments that need review">
                        <thead><tr><th>Waiting</th><th>Arrived</th><th>Provider</th><th>Account</th><th>For</th><th>Amount</th>
                            <th>What happened</th><th>Reference</th><th></th></tr></thead>
                        <tbody>{page.items.map(row => <tr key={`${row.provider}:${row.event_id}`}>
                            <td>{waitingText(row.waiting_hours)}</td>
                            <td>{new Date(row.received_at).toLocaleString()}</td>
                            <td>{row.provider === 'stripe' ? 'Stripe' : 'PayPal'}</td>
                            <td>{accountText(row)}</td>
                            <td>{kindText(row.kind)}</td>
                            <td>{amountText(row)}</td>
                            <td>{row.reason_text}</td>
                            <td style={{ overflowWrap: 'anywhere' }}>{row.provider_reference ?? row.event_id}</td>
                            <td>{row.can_close
                                ? <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => choose(row)}>Mark as handled</button>
                                : <span>{row.close_blocked_reason}</span>}</td>
                        </tr>)}</tbody></table></div>}
                <p className="form-help">For PayPal the amount is the price of our order; for Stripe it is the amount in Stripe's message.</p>
            </>}
            {selected && <section aria-label="Close a payment review">
                <h3 style={{ overflowWrap: 'anywhere' }}>{selected.provider === 'stripe' ? 'Stripe' : 'PayPal'} {selected.event_id}</h3>
                <fieldset disabled={busy}>
                    <div className="form-group"><label>What did you check and do? (at least {MIN_NOTE} characters)
                        <textarea className="form-input" value={note} maxLength={2000} onChange={event => setNote(event.target.value)} /></label></div>
                    <label><input type="checkbox" checked={checked} onChange={event => setChecked(event.target.checked)} />I checked this payment at Stripe or PayPal and settled it by hand where needed.</label>
                </fieldset>
                <button className="btn btn-primary" disabled={busy || !checked || note.trim().length < MIN_NOTE} onClick={() => setConfirming(true)}>Close this review</button>
                <button className="btn btn-secondary" disabled={busy} onClick={() => setSelected(null)}>Cancel</button>
            </section>}
            <ConfirmationModal isOpen={confirming} title="Close this review?"
                message="It leaves this list and the payments alert. Nothing is given or taken back. Your name and note are recorded."
                confirmLabel="Close review" variant="warning"
                onConfirm={() => { setConfirming(false); void close(); }} onCancel={() => setConfirming(false)} />
        </div>
    </section>;
}
