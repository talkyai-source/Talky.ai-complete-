import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useAuthGeneration } from '../lib/use-auth-generation';
import { useOperationOwner } from '../lib/use-operation-owner';
import { useDialogFocus } from '../lib/use-dialog-focus';
import { isHoldUuid, matchesHoldStatus, matchesHoldPropagation, matchesHoldReceipt, reconcileHoldIntent } from '../lib/formal-holds';
import type { FormalHoldStatus, HoldIntent, HoldPropagation, HoldReceipt, HoldTarget, HoldType } from '../lib/formal-holds';
import './FormalHoldPanel.css';

type Outcome = { kind: 'saved'; intent: HoldIntent; receipt: HoldReceipt }
    | { kind: 'unknown'; intent: HoldIntent } | { kind: 'observed'; eventId: string };
// Keep an unresolved operation when a dialog is closed/reopened in this session.
// This is not server idempotency and is deliberately discarded on identity change.
const outcomes = new Map<string, Outcome>();
api.onAuthGenerationChanged(() => outcomes.clear());
const actionLabels = { hold: 'Create formal hold', restore: 'Restore selected hold', appeal: 'Submit operator appeal', review: 'Review appeal' };
const displayTime = (value: string | null | undefined) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : 'Not recorded';

type FormalHoldPanelProps = { target: HoldTarget; onClose: () => void; onChanged?: () => void;
    returnFocusFallback?: RefObject<HTMLElement | null> };

export function FormalHoldPanel(props: FormalHoldPanelProps) {
    const generation = useAuthGeneration();
    return <OwnedFormalHoldPanel key={`${generation}:${props.target.type}:${props.target.id}`} {...props} generation={generation} />;
}

function OwnedFormalHoldPanel({ target, onClose, onChanged, generation, returnFocusFallback }: FormalHoldPanelProps & { generation: number }) {
    const { user, isAuthenticated } = useAuth();
    const allowed = isAuthenticated && user?.role === 'platform_admin' && isHoldUuid(target.id);
    const scope = `${target.type}:${target.id}:${user?.id}:${allowed}`;
    const captureOwner = useOperationOwner(generation, scope);
    const cacheKey = `${generation}:${target.type}:${target.id}`;
    const [status, setStatus] = useState<FormalHoldStatus | null>(null);
    const [selected, setSelected] = useState('');
    const captureSelection = useOperationOwner(generation, `${scope}:${selected}`);
    const [readError, setReadError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [proof, setProof] = useState<HoldPropagation | null>(null);
    const [proofError, setProofError] = useState<string | null>(null);
    const [proofLoading, setProofLoading] = useState(false);
    const [proofRevision, setProofRevision] = useState(0);
    const [outcome, setOutcome] = useState<Outcome | null>(() => outcomes.get(cacheKey) ?? null);
    const outcomeRef = useRef(outcome);
    const [mode, setMode] = useState<HoldIntent['kind'] | null>(null);
    const [type, setType] = useState<HoldType>('ADMIN');
    const [category, setCategory] = useState('administrative');
    const [reason, setReason] = useState('');
    const [evidence, setEvidence] = useState('');
    const [duration, setDuration] = useState('24');
    const [immediate, setImmediate] = useState(true);
    const [decision, setDecision] = useState<'granted' | 'denied'>('denied');
    const [reviewed, setReviewed] = useState<HoldIntent | null>(null);
    const reviewedRef = useRef<HoldIntent | null>(null);
    const rememberReview = useCallback((intent: HoldIntent | null) => { reviewedRef.current = intent; setReviewed(intent); }, []);
    const [mutationError, setMutationError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const pending = useRef(false), readVersion = useRef(0), proofVersion = useRef(0);
    const dialog = useRef<HTMLDivElement>(null);
    const refreshControl = useRef<HTMLButtonElement>(null), draftForm = useRef<HTMLFormElement>(null);
    const reviewPanel = useRef<HTMLElement>(null), actionControls = useRef<HTMLDivElement>(null);
    const changed = useRef(onChanged);
    useLayoutEffect(() => { changed.current = onChanged; }, [onChanged]);
    const titleId = useId();
    useDialogFocus(true, dialog, onClose, busy, returnFocusFallback);
    const focusBeforeChange = useCallback((...regions: Array<HTMLElement | null | undefined>) => {
        if (!captureOwner()()) return;
        if (regions.some((region) => region?.isConnected && region.contains(document.activeElement))) dialog.current?.focus();
    }, [captureOwner]);
    const remember = useCallback((next: Outcome) => {
        outcomeRef.current = next; outcomes.set(cacheKey, next); setOutcome(next);
    }, [cacheKey]);

    const load = useCallback(async () => {
        const owns = captureOwner(), version = ++readVersion.current;
        if (!allowed || !owns()) return;
        focusBeforeChange(refreshControl.current, reviewPanel.current, actionControls.current,
            draftForm.current?.querySelector<HTMLButtonElement>('button[type="submit"]'));
        setLoading(true); setReadError(null);
        // Retire reviewed callbacks before the refresh can yield or fail.
        if (reviewedRef.current) { rememberReview(null); setMutationError('Status was refreshed. Review the action again before confirming.'); }
        try {
            const response = await api.getFormalHoldStatus(target);
            if (!owns() || version !== readVersion.current) return;
            if (response.error || !matchesHoldStatus(response.data, target)) {
                setReadError(response.error?.message ?? 'The returned hold status does not match this account or is incomplete.');
                return;
            }
            const next = response.data;
            setStatus(next);
            const unresolved = outcomeRef.current;
            if (unresolved?.kind === 'saved') {
                const savedEvent = [next.active_suspension, ...next.suspension_history].find((e) => e?.suspension_id === unresolved.receipt.suspension_id);
                const reflectsReceipt = unresolved.intent.kind === 'hold' ? Boolean(savedEvent)
                    : Boolean(reconcileHoldIntent(next, unresolved.intent));
                if (!reflectsReceipt) setReadError('The saved action receipt is retained, but the refreshed status does not yet show that exact outcome. Refresh to reconcile before another action.');
            }
            if (unresolved?.kind === 'unknown') {
                const observed = reconcileHoldIntent(next, unresolved.intent);
                if (observed) {
                    remember({ kind: 'observed', eventId: observed.suspension_id }); setSelected(observed.suspension_id);
                    rememberReview(null); setMode(null); setReason(''); setMutationError(null);
                    changed.current?.();
                }
            }
            setSelected((previous) => previous || next.active_suspension?.suspension_id || next.suspension_history[0]?.suspension_id || '');
        } catch (error) {
            if (owns() && version === readVersion.current) setReadError(error instanceof Error ? error.message : 'Hold status is unavailable.');
        } finally {
            if (owns() && version === readVersion.current) setLoading(false);
        }
    }, [allowed, captureOwner, target, remember, rememberReview, focusBeforeChange]);
    useEffect(() => { void load(); return () => { readVersion.current += 1; }; }, [load]);

    useEffect(() => {
        const owns = captureSelection(), version = ++proofVersion.current;
        setProof(null); setProofError(null); setProofLoading(false);
        if (!allowed || !selected || !owns()) return;
        setProofLoading(true);
        void (async () => {
            try {
                const response = await api.getFormalHoldPropagation(selected);
                if (!owns() || version !== proofVersion.current) return;
                if (response.error || !matchesHoldPropagation(response.data, target, selected)) {
                    setProofError(response.error?.message ?? 'The evidence does not match the selected hold or is incomplete.');
                } else setProof(response.data);
            } catch (error) {
                if (owns() && version === proofVersion.current) setProofError(error instanceof Error ? error.message : 'Enforcement evidence is unavailable.');
            } finally {
                if (owns() && version === proofVersion.current) setProofLoading(false);
            }
        })();
        return () => { proofVersion.current += 1; };
    }, [allowed, selected, target, captureSelection, proofRevision]);

    const events = status ? [...new Map([status.active_suspension, ...status.suspension_history]
        .filter((event) => event !== null).map((event) => [event.suspension_id, event])).values()] : [];
    const event = events.find((item) => item.suspension_id === selected);
    const blocked = !allowed || !status || loading || Boolean(readError) || busy || outcome?.kind === 'unknown';
    const eventActive = event?.is_active === true;
    const alreadySaved = (kind: HoldIntent['kind']) => {
        if (outcome?.kind !== 'saved' || outcome.intent.kind !== kind) return false;
        if (kind === 'hold') return events.find((e) => e.suspension_id === outcome.receipt.suspension_id)?.is_active !== false;
        return outcome.receipt.suspension_id === selected;
    };
    const canSubmitAppeal = eventActive && !event?.appeal_decision && !event?.appeal_submitted_at;
    const canReview = eventActive && event?.appeal_decision === 'pending';
    const resetDraft = () => { focusBeforeChange(draftForm.current, reviewPanel.current); setMode(null); setReason(''); setEvidence(''); rememberReview(null); setMutationError(null); };
    const begin = (next: HoldIntent['kind']) => { resetDraft(); setMode(next); };
    const choose = (id: string) => { if (pending.current) return; resetDraft(); setSelected(id); };

    const review = () => {
        if (blocked || !mode || !user || alreadySaved(mode)) return;
        const text = reason.trim();
        if (!text || text.length > 4000) { setMutationError('Enter a reason between 1 and 4000 characters.'); return; }
        setMutationError(null);
        if (mode === 'hold') {
            if (status?.active_suspension) return;
            const timed = type === 'TEMPORARY' || type === 'BILLING';
            if (!category.trim() || (timed && (!Number.isSafeInteger(Number(duration)) || Number(duration) <= 0))) {
                setMutationError('Enter a category and a positive whole number of hours for a timed hold.'); return;
            }
            focusBeforeChange(draftForm.current);
            rememberReview({ kind: 'hold', target, actorId: user.id, previousIds: events.map((e) => e.suspension_id), immediate,
                request: { suspension_type: type, reason_category: category.trim(), reason_description: text,
                    ...(timed ? { duration_hours: Number(duration) } : {}),
                    evidence: { operator_request_id: crypto.randomUUID(), ...(evidence.trim() ? { operator_note: evidence.trim() } : {}) }, notify_user: false } });
        } else if (eventActive && event) {
            if ((mode === 'appeal' && !canSubmitAppeal) || (mode === 'review' && !canReview)) return;
            focusBeforeChange(draftForm.current);
            rememberReview(mode === 'review' ? { kind: mode, target, eventId: event.suspension_id, reason: text, decision }
                : { kind: mode, target, eventId: event.suspension_id, reason: text });
        }
    };

    const commit = async () => {
        const owns = captureSelection();
        if (!reviewed || reviewed !== reviewedRef.current || blocked || pending.current || !owns() || alreadySaved(reviewed.kind)) return;
        const intent = reviewed;
        reviewedRef.current = null;
        // Busy disables every button/select in this review; hand focus off before the render.
        focusBeforeChange(dialog.current?.querySelector<HTMLElement>('button:focus, select:focus'));
        pending.current = true; setBusy(true); setMutationError(null);
        // A browser response may be lost even when the transaction committed.
        remember({ kind: 'unknown', intent });
        try {
            const response = await (intent.kind === 'hold' ? api.createFormalHold(target, intent.request, intent.immediate)
                : intent.kind === 'restore' ? api.restoreFormalHold(target, intent.eventId, intent.reason)
                    : intent.kind === 'appeal' ? api.submitFormalHoldAppeal(intent.eventId, intent.reason)
                        : api.reviewFormalHoldAppeal(intent.eventId, intent.decision, intent.reason));
            if (!owns()) return;
            if (response.error) {
                setMutationError(response.error.message);
                if ([400, 401, 403, 404, 409, 422].includes(response.error.status ?? 0)) {
                    outcomes.delete(cacheKey); outcomeRef.current = null; setOutcome(null); rememberReview(null);
                    // A rejection can mean a newer hold/decision replaced this one.
                    void load();
                }
                return;
            }
            if (!matchesHoldReceipt(response.data, intent)) {
                setMutationError('The action response could not be matched to the reviewed account and hold. Inspect saved status before any further action.');
                return;
            }
            remember({ kind: 'saved', intent, receipt: response.data });
            rememberReview(null); setMode(null); setReason(''); setSelected(response.data.suspension_id);
            // The authoritative receipt is independent of either following read.
            void load(); setProofRevision((value) => value + 1);
            changed.current?.();
        } catch (error) {
            if (owns()) setMutationError(error instanceof Error ? error.message : 'The action outcome is unconfirmed.');
        } finally {
            if (owns()) { pending.current = false; setBusy(false); }
        }
    };

    return <div className="modal-overlay">
        <div className="modal-content formal-hold-panel" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={dialog}>
            <div className="modal-header"><h2 id={titleId}>Formal holds · {target.label}</h2>
                <button className="btn btn-secondary" type="button" aria-label="Close formal holds" disabled={busy} onClick={onClose}>Close</button></div>
            <div className="modal-body">
                <p>{target.type} ID: <code>{target.id}</code></p>
                <p>Formal holds have a reason, audit record and exact-event restoration. Ordinary user blocks and tenant subscription controls are separate. Restoring one hold may leave another restriction in place.</p>
                {!allowed ? <p role="alert">A current platform administrator is required to inspect or change formal holds.</p> : <>
                    <button ref={refreshControl} className="btn btn-secondary formal-hold-refresh" type="button" disabled={loading || busy} onClick={() => { void load(); setProofRevision((v) => v + 1); }}>Refresh hold status</button>
                    {loading && <p role="status">Loading formal hold status…</p>}
                    {readError && <p role="alert">Status unavailable: {readError}{status ? ' Previously loaded status is stale; actions are disabled.' : ''}</p>}
                    {mutationError && <p role="alert">{mutationError}</p>}
                    {outcome?.kind === 'saved' && <section role="status" aria-label="Saved formal action receipt">
                        <strong>Action saved: {actionLabels[outcome.intent.kind]}</strong>
                        <p>Hold <code>{outcome.receipt.suspension_id}</code> · Audit <code>{outcome.receipt.audit_event_id}</code></p>
                        {outcome.receipt.effective_status && <p>Reported account status: {outcome.receipt.effective_status}</p>}
                        {outcome.receipt.restoration_warnings?.map((warning) => <p key={warning}>{warning}</p>)}
                        <p>Saved action is separate from downstream enforcement and notification delivery. See the evidence below.</p>
                    </section>}
                    {outcome?.kind === 'unknown' && <p role="alert">Action outcome unconfirmed. Further writes are paused. Refresh hold status to inspect the exact saved state; this request will not be automatically repeated.</p>}
                    {outcome?.kind === 'observed' && <p role="status">Matching saved state observed for hold <code>{outcome.eventId}</code>. The original request receipt was unavailable; this does not prove which request committed it.</p>}
                    {status && <>
                        <p>Hold workflow state: {status.status}. Current effective status: {status.effective_status ?? 'Not provided'}.</p>
                        {status.remaining_hold_ids.length > 0 && <p>Remaining restriction IDs: {status.remaining_hold_ids.join(', ')}</p>}
                        {!status.active_suspension && <p>No direct active formal hold is recorded for this {target.type}. Parent or independent restrictions may still apply.</p>}
                        {!status.history_complete && <p role="status">History is limited to {status.history_limit} records. Older events are not included; an absent event is not evidence that an uncertain request failed.</p>}
                        {events.length === 0 ? <p>No formal hold history recorded.</p> : <label>Reviewed hold
                            <select className="form-input" aria-label="Reviewed hold" disabled={busy} value={selected} onChange={(e) => choose(e.target.value)}>
                                {!events.some((e) => e.suspension_id === selected) && <option value={selected}>{selected || 'Select a hold'} (not in loaded history)</option>}
                                {events.map((e) => <option key={e.suspension_id} value={e.suspension_id}>{e.suspension_id} · {e.is_active ? 'Active' : 'Restored'} · {e.suspension_type}</option>)}
                            </select></label>}
                        {event && <section aria-label="Selected hold details">
                            <p>{event.suspension_type} · {event.is_active ? 'Active hold' : 'Restored hold'} · Reason: {event.reason_description}</p>
                            <p>Created {displayTime(event.suspended_at)} · Scheduled expiry {displayTime(event.suspended_until)} · Restored {displayTime(event.restored_at)}</p>
                            <p>Appeal: {event.appeal_decision ?? 'None'}{event.appeal_reason ? ` · ${event.appeal_reason}` : ''}</p>
                            {event.appeal_response && <p>Review response: {event.appeal_response}</p>}
                            {typeof event.evidence?.operator_note === 'string' && <p>Supporting note: {event.evidence.operator_note}</p>}
                        </section>}
                        <div ref={actionControls} className="formal-hold-actions">
                            <button className="btn btn-secondary" disabled={blocked || alreadySaved('hold') || Boolean(status.active_suspension)} onClick={() => begin('hold')}>Create formal hold</button>
                            <button className="btn btn-secondary" disabled={blocked || alreadySaved('restore') || !eventActive} onClick={() => begin('restore')}>Restore selected hold</button>
                            <button className="btn btn-secondary" disabled={blocked || alreadySaved('appeal') || !canSubmitAppeal} onClick={() => begin('appeal')}>Submit operator appeal</button>
                            <button className="btn btn-secondary" disabled={blocked || alreadySaved('review') || !canReview} onClick={() => begin('review')}>Review appeal</button>
                        </div>
                    </>}
                    {mode && !reviewed && <form ref={draftForm} onSubmit={(e) => { e.preventDefault(); review(); }}>
                        <h3>{actionLabels[mode]}</h3>
                        {mode === 'hold' && <>
                            <label>Hold type<select className="form-input" aria-label="Hold type" value={type} onChange={(e) => setType(e.target.value as HoldType)}>
                                {(['ADMIN', 'TEMPORARY', 'BILLING', 'ABUSE', 'COMPLIANCE', 'EMERGENCY'] as HoldType[]).map((t) => <option key={t}>{t}</option>)}</select></label>
                            <label>Reason category<input className="form-input" aria-label="Reason category" required value={category} onChange={(e) => setCategory(e.target.value)} /></label>
                            {(type === 'TEMPORARY' || type === 'BILLING') && <label>Duration in hours<input className="form-input" aria-label="Duration in hours" type="number" min="1" step="1" required value={duration} onChange={(e) => setDuration(e.target.value)} /></label>}
                            {target.type === 'tenant' && <label className="formal-hold-check"><input type="checkbox" checked={immediate} onChange={(e) => setImmediate(e.target.checked)} />End existing calls under this formal hold</label>}
                            <p>{target.type === 'user' ? 'This revokes this user’s credentials. It does not terminate every call in their tenant.' : immediate || target.type === 'partner' ? 'Existing calls require original-provider termination proof. A saved hold alone does not prove they have ended.' : 'New calls are blocked. Only proven established calls eligible under the saved policy may continue.'}</p>
                            <label>Supporting note (optional)<textarea className="form-input" aria-label="Supporting note" rows={3} maxLength={4000} value={evidence} onChange={(e) => setEvidence(e.target.value)} /></label>
                        </>}
                        {mode === 'review' && <label>Decision<select className="form-input" aria-label="Appeal decision" value={decision} onChange={(e) => setDecision(e.target.value as 'granted' | 'denied')}><option value="denied">Deny appeal</option><option value="granted">Grant appeal and restore this hold</option></select></label>}
                        <label>{mode === 'review' ? 'Review response' : 'Reason'}<textarea className="form-input" aria-label="Formal action reason" rows={4} required maxLength={4000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
                        <div className="formal-hold-actions"><button className="btn btn-secondary" type="button" onClick={resetDraft}>Cancel draft</button><button className="btn btn-primary" type="submit" disabled={blocked}>Review action</button></div>
                    </form>}
                    {reviewed && <section ref={reviewPanel} aria-label="Review formal action">
                        <h3>Confirm {actionLabels[reviewed.kind].toLowerCase()}</h3>
                        <p>{target.label} · {target.type} <code>{target.id}</code></p>
                        {'eventId' in reviewed && <p>Exact reviewed hold: <code>{reviewed.eventId}</code></p>}
                        <p>{reviewed.kind === 'hold' ? reviewed.request.reason_description : reviewed.reason}</p>
                        {reviewed.kind === 'hold' && <p>{reviewed.request.suspension_type} · {reviewed.request.duration_hours ? `${reviewed.request.duration_hours} hours` : 'Manual restoration required'} · {target.type === 'tenant' && !reviewed.immediate ? 'Established-call policy exception requested' : 'Immediate policy'}</p>}
                        {reviewed.kind === 'review' && <p>Decision: {reviewed.decision}</p>}
                        <div className="formal-hold-actions"><button className="btn btn-secondary" disabled={busy || outcome?.kind === 'unknown'} onClick={() => { focusBeforeChange(reviewPanel.current); rememberReview(null); }}>Edit draft</button>
                        <button className="btn btn-primary" disabled={blocked} onClick={() => void commit()}>Confirm formal action</button></div>
                    </section>}
                    {selected && <section aria-label="Formal hold enforcement evidence"><h3>Enforcement evidence</h3>
                        {proofLoading && <p role="status">Loading enforcement evidence…</p>}
                        {proofError && <p role="alert">Evidence unavailable: {proofError}</p>}
                        {proof && <>
                            <p>Observed {displayTime(proof.observed_at_db)} · Policy cutoff {displayTime(proof.policy_cutoff)}</p>
                            <p>Selected event: {proof.event_state} · Current authority: {proof.current_authority.status} · Enforcement: {proof.propagation_status}</p>
                            {proof.current_authority.remaining_hold_ids.length > 0 && <p>Current restriction IDs: {proof.current_authority.remaining_hold_ids.join(', ')}</p>}
                            {proof.dimensions.map((dimension) => <section key={dimension.key} aria-label={`${dimension.key} evidence`}>
                                <h4>{dimension.key.replaceAll('_', ' ')}: {dimension.status.replaceAll('_', ' ')}</h4>
                                <p>Coverage: {dimension.coverage}. Captured {dimension.counts.captured}; observed {dimension.counts.observed}; confirmed {dimension.counts.confirmed}; pending {dimension.counts.pending}; exempt {dimension.counts.exempt}; unknown {dimension.counts.unknown}.</p>
                                {dimension.reasons.map((r) => <p key={r}>{r}</p>)}
                            </section>)}
                            {proof.reconciliation_reasons.map((r) => <p key={r}>{r}</p>)}
                        </>}
                    </section>}
                </>}
            </div>
        </div>
    </div>;
}
