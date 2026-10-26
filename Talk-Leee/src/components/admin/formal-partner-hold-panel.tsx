"use client";

import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { getRequestIdentityGeneration, isApiClientError } from "@/lib/http-client";
import { formalPartnerHolds, holdTypes, type HoldType, type PartnerHoldCommand, type PartnerHoldEvidence, type PartnerHoldReceipt, type PartnerHoldStatus } from "@/lib/formal-partner-holds";

type Mode = PartnerHoldCommand["kind"];
const labels: Record<Mode,string> = {suspend:"Create formal hold",restore:"Restore selected hold",appeal:"Record operator appeal",granted:"Grant appeal",denied:"Deny appeal"};
const control = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm";
const message = (error: unknown) => error instanceof Error ? error.message : "The request could not be completed.";

type Props = {partnerId:string; partnerName:string; operatorId:string; identityGeneration:number; onClose:()=>void; onChanged?:()=>void};
type SavedOutcome = {unknown?:PartnerHoldCommand; receipt?:PartnerHoldReceipt; createdId?:string; retired:string[]; completed:string[]};
const outcomes = new Map<string,SavedOutcome>();
let outcomeGeneration = -1;
function rememberedOutcome(operatorId:string, generation:number, partnerId:string):SavedOutcome {
    if (generation !== outcomeGeneration) { outcomes.clear(); outcomeGeneration = generation; }
    const key = `${operatorId}:${partnerId}`;
    let value = outcomes.get(key);
    if (!value) { value = {retired:[],completed:[]}; outcomes.set(key,value); }
    return value;
}
export function FormalPartnerHoldPanel(props:Props) {
    return <PartnerHoldBody key={`${props.operatorId}:${props.identityGeneration}:${props.partnerId}`} {...props}/>;
}
function PartnerHoldBody({partnerId,partnerName,operatorId,identityGeneration,onClose,onChanged}:Props) {
    const [memory] = useState(() => rememberedOutcome(operatorId,identityGeneration,partnerId));
    const id = useId();
    const alive = useRef(true);
    const sequence = useRef(0);
    const submitting = useRef(false);
    const changedCallback = useRef(onChanged);
    const closeButton = useRef<HTMLButtonElement>(null);
    const refreshButton = useRef<HTMLButtonElement>(null);
    const reviewButton = useRef<HTMLButtonElement>(null);
    const submitButton = useRef<HTMLButtonElement>(null);
    const draftForm = useRef<HTMLFormElement>(null);
    const actionControls = useRef<HTMLDivElement>(null);
    const retiredEvents = useRef(new Set(memory.retired));
    const committedActions = useRef(new Set(memory.completed));
    const createdEvent = useRef<string | undefined>(memory.createdId);
    const [createdId,setCreatedId] = useState(memory.createdId);
    const [retiredIds,setRetiredIds] = useState(memory.retired);
    const [completed,setCompleted] = useState(memory.completed);
    const revisionRef = useRef(0);
    const [revision,setRevision] = useState(0);
    const [requestId,setRequestId] = useState("");
    const [status,setStatus] = useState<PartnerHoldStatus>();
    const [loading,setLoading] = useState(true);
    const [readError,setReadError] = useState("");
    const [eventId,setEventId] = useState("");
    const [evidence,setEvidence] = useState<PartnerHoldEvidence>();
    const [evidenceError,setEvidenceError] = useState("");
    const [proofRevision,setProofRevision] = useState(0);
    const [mode,setMode] = useState<Mode|null>(null);
    const [reason,setReason] = useState("");
    const [category,setCategory] = useState("administrative_review");
    const [holdType,setHoldType] = useState<HoldType>("ADMIN");
    const [hours,setHours] = useState("24");
    const [reviewed,setReviewed] = useState(false);
    const [busy,setBusy] = useState(false);
    const [actionError,setActionError] = useState("");
    const [parentRefreshError,setParentRefreshError] = useState("");
    const [receipt,setReceipt] = useState(memory.receipt);
    const [unknown,setUnknown] = useState(memory.unknown);
    const owned = useCallback(() => alive.current && getRequestIdentityGeneration() === identityGeneration,[identityGeneration]);
    useLayoutEffect(() => { changedCallback.current = onChanged; },[onChanged]);
    const retainFocus = useCallback((...affected:(HTMLElement|null)[]) => {
        if (!owned()) return;
        const focused = document.activeElement;
        if (focused && affected.some(node => node?.contains(focused))) closeButton.current?.focus({preventScroll:true});
    },[owned]);

    const reload = useCallback(async () => {
        if (!owned()) return;
        retainFocus(refreshButton.current,reviewButton.current,submitButton.current,actionControls.current);
        revisionRef.current += 1; setRevision(value => value + 1); setReviewed(false);
        const ticket = ++sequence.current;
        setLoading(true); setReadError("");
        try {
            const value = await formalPartnerHolds.status(partnerId);
            if (!owned() || ticket !== sequence.current) return;
            const created = value.suspension_history.find(event => event.suspension_id === createdEvent.current);
            if (created && !created.is_active && created.restored_at && created.audit_log_id) {
                retiredEvents.current.add(created.suspension_id);
                memory.retired = [...retiredEvents.current];
                setRetiredIds(previous => previous.includes(created.suspension_id) ? previous : [...previous,created.suspension_id]);
            }
            setStatus(value);
            setEventId(previous => previous || value.active_suspension?.suspension_id || value.suspension_history[0]?.suspension_id || "");
            setProofRevision(previous => previous + 1);
        } catch (error) {
            if (owned() && ticket === sequence.current) setReadError(message(error));
        } finally {
            if (owned() && ticket === sequence.current) setLoading(false);
        }
    },[owned,partnerId,memory,retainFocus]);
    useEffect(() => {
        alive.current = true;
        const previousFocus = document.activeElement as HTMLElement | null;
        const initialRead = window.setTimeout(() => void reload(), 0);
        return () => { window.clearTimeout(initialRead); alive.current = false; sequence.current += 1; if (previousFocus?.isConnected) previousFocus.focus(); };
    },[reload]);
    useEffect(() => {
        let current = true;
        const controller = new AbortController();
        async function read() {
            setEvidence(undefined); setEvidenceError("");
            if (!eventId) return;
            try {
                const value = await formalPartnerHolds.evidence(partnerId,eventId,controller.signal);
                if (current && owned()) setEvidence(value);
            } catch (error) {
                if (current && owned()) setEvidenceError(message(error));
            }
        }
        void read();
        return () => { current = false; controller.abort(); };
    },[eventId,owned,partnerId,proofRevision]);

    const events = status ? [status.active_suspension,...status.suspension_history].filter((event,index,all) => event && all.findIndex(other => other?.suspension_id === event.suspension_id) === index) : [];
    const selected = events.find(event => event?.suspension_id === eventId);
    const disabled = busy || loading || Boolean(readError) || Boolean(unknown) || !owned();
    const retired = retiredIds.includes(eventId);
    const createBlocked = Boolean(createdId && !retiredIds.includes(createdId));
    function invalidateDraft() { revisionRef.current += 1; setRevision(value => value + 1); setReviewed(false); }
    function choose(next:Mode) { invalidateDraft(); setMode(next); setReason(""); setRequestId(crypto.randomUUID()); setActionError(""); }

    async function submit() {
        if (!mode || !owned() || disabled || submitting.current || memory.unknown || !reviewed || revisionRef.current !== revision) return;
        if (mode !== "suspend" && (retiredEvents.current.has(eventId) || committedActions.current.has(`${eventId}:${mode}`))) return;
        if (!reason.trim() || reason.trim().length > 4000) { setActionError("Enter a reason of up to 4,000 characters."); return; }
        if (mode === "suspend" && (status?.active_suspension || !category.trim() || (createdEvent.current && !retiredEvents.current.has(createdEvent.current)))) return;
        if (mode !== "suspend" && (!selected?.is_active || !eventId)) return;
        if (mode === "suspend" && (holdType === "TEMPORARY" || holdType === "BILLING") && (!Number.isInteger(Number(hours)) || Number(hours) < 1)) {
            setActionError("Enter a positive whole number of hours."); return;
        }
        const command: PartnerHoldCommand = mode === "suspend"
            ? {kind:mode,reason:reason.trim(),category:category.trim(),holdType,operatorRequestId:requestId,...((holdType === "TEMPORARY" || holdType === "BILLING") ? {hours:Number(hours)} : {})}
            : {kind:mode,eventId,reason:reason.trim()};
        memory.unknown = command; // Unmount is not cancellation; retain before the first await.
        retainFocus(draftForm.current,refreshButton.current,reviewButton.current,actionControls.current);
        submitting.current = true; invalidateDraft(); setBusy(true); setActionError("");
        try {
            const result = await formalPartnerHolds.change(partnerId,command);
            if (!owned()) return;
            const firstAcceptance = recordSaved(command.kind,result.suspension_id);
            memory.receipt = result; memory.unknown = undefined;
            setReceipt(result); setUnknown(undefined); setEventId(result.suspension_id); setMode(null); setReviewed(false);
            notifyChanged(result,firstAcceptance);
            if (owned()) await reload();
        } catch (error) {
            if (!owned()) return;
            setActionError(message(error));
            // A rejected audited mutation is distinct from a lost response.
            // Unknown requests are never resent from a refresh callback.
            if (!(isApiClientError(error) && [400,401,403,404,409,422].includes(error.status ?? 0))) setUnknown(command);
            else memory.unknown = undefined;
        } finally {
            submitting.current = false;
            if (owned()) setBusy(false);
        }
    }

    function recordSaved(kind:Mode,savedId:string) {
        const key = `${savedId}:${kind}`;
        if (committedActions.current.has(key)) return false;
        committedActions.current.add(key); setCompleted(previous => [...previous,key]);
        memory.completed = [...committedActions.current];
        if (kind === "suspend") { createdEvent.current = savedId; memory.createdId = savedId; setCreatedId(savedId); }
        if (kind === "restore" || kind === "granted") { retiredEvents.current.add(savedId); memory.retired = [...retiredEvents.current]; setRetiredIds(memory.retired); }
        return true;
    }

    function notifyChanged(saved:PartnerHoldReceipt,firstAcceptance:boolean) {
        if (!firstAcceptance || !owned()) return;
        setParentRefreshError("");
        const failed = () => {
            if (owned() && memory.receipt === saved) setParentRefreshError("Related account views could not be refreshed. The saved decision is retained; refresh the partner directory.");
        };
        // Parent reads are separate from the accepted write. A failed callback
        // must never revive its unknown command or erase the saved receipt.
        try { void Promise.resolve(changedCallback.current?.()).catch(failed); }
        catch { failed(); }
    }

    function reconcile() {
        if (!unknown || memory.unknown !== unknown || !status || loading || readError || !owned()) return;
        const matchingCreations = unknown.kind === "suspend" ? events.filter(event => event?.audit_log_id
            && (event.is_active || Boolean(event.restored_at)) && event.suspended_by === operatorId
            && event.evidence?.operator_request_id === unknown.operatorRequestId && event.suspension_type === unknown.holdType
            && event.reason_description === unknown.reason && event.reason_category === unknown.category
            && (unknown.hours === undefined ? event.suspended_until === null : event.suspended_until !== null
                && Date.parse(event.suspended_until) - Date.parse(event.suspended_at) === unknown.hours * 3_600_000)) : [];
        if (unknown.kind === "suspend" && matchingCreations.length !== 1) {
            setActionError("No matching saved decision is available. Keep this outcome unverified; do not repeat the request."); return;
        }
        const event = unknown.kind === "suspend" ? matchingCreations.length === 1 ? matchingCreations[0] : undefined
            : events.find(item => item?.suspension_id === unknown.eventId);
        if (!event?.audit_log_id) return;
        const matches = unknown.kind === "suspend"
            ? true
            : unknown.kind === "restore" ? !event.is_active && Boolean(event.restored_at) && event.restore_reason === unknown.reason
            : unknown.kind === "appeal" ? event.appeal_reason === unknown.reason && (event.appeal_decision === "pending"
                || ((event.appeal_decision === "granted" || event.appeal_decision === "denied") && Boolean(event.appeal_submitted_at)))
            : event.appeal_decision === unknown.kind && event.appeal_response === unknown.reason && (unknown.kind !== "granted" || (!event.is_active && Boolean(event.restored_at)));
        if (!matches) { setActionError("No matching saved decision is available. Keep this outcome unverified; do not repeat the request."); return; }
        retainFocus(reviewButton.current,draftForm.current);
        const firstAcceptance = recordSaved(unknown.kind,event.suspension_id);
        if (unknown.kind === "suspend" && !event.is_active) {
            retiredEvents.current.add(event.suspension_id); memory.retired = [...retiredEvents.current]; setRetiredIds(memory.retired);
        }
        invalidateDraft();
        memory.receipt = {suspension_id:event.suspension_id,audit_event_id:event.audit_log_id,status:"Matching saved decision reviewed"};
        memory.unknown = undefined;
        setReceipt(memory.receipt); setEventId(event.suspension_id);
        setUnknown(undefined); setMode(null); setActionError("");
        notifyChanged(memory.receipt,firstAcceptance);
    }

    return <Modal open onOpenChange={open => { if (!open) onClose(); }} title={`Formal holds: ${partnerName}`} description={`Partner ${partnerId}. Formal security restrictions are separate from ordinary account status.`} size="lg" footer={<Button ref={closeButton} variant="outline" onClick={onClose}>Close</Button>}>
        <div className="space-y-5">
            {receipt && <section role="status" className="rounded-lg border border-emerald-500/40 p-3">
                <p className="font-medium">{receipt.status === "Matching saved decision reviewed" ? receipt.status : "Saved decision confirmed"}</p>
                <p className="break-all text-sm">Hold: {receipt.suspension_id}<br/>{receipt.status === "Matching saved decision reviewed" ? "Saved event audit reference" : "Audit receipt"}: {receipt.audit_event_id}</p>
                {receipt.status === "Matching saved decision reviewed" && <p className="text-sm">Original request receipt unavailable. This observed state does not identify which request committed it.</p>}
                {receipt.effective_status && <p>Account restriction reported with this decision: {receipt.effective_status}</p>}
                {!!receipt.remaining_hold_ids?.length && <p>Other holds reported with this decision: {receipt.remaining_hold_ids.length}</p>}
                {receipt.restoration_warnings?.map(warning => <p key={warning}>{warning}</p>)}
                {parentRefreshError && <p role="alert">{parentRefreshError}</p>}
                <Button variant="ghost" onClick={() => { memory.receipt = undefined; setReceipt(undefined); }}>Dismiss receipt</Button>
            </section>}
            {unknown && <section role="alert" className="rounded-lg border border-amber-500/50 p-3">
                <p>The request outcome is unverified. Refresh and inspect the saved decision before any further action.</p>
                <Button ref={reviewButton} variant="outline" disabled={loading || busy || Boolean(readError)} onClick={reconcile}>Review matching saved decision</Button>
            </section>}
            {actionError && <p role="alert">{actionError}</p>}
            <Button ref={refreshButton} variant="outline" onClick={() => void reload()} disabled={busy || loading}>{loading ? "Loading holds…" : "Refresh status and evidence"}</Button>
            {readError && <p role="alert">Status unavailable: {readError}. Current account status could not be verified.</p>}
            {status && <>
                <p className="font-medium">Last successful status read</p>
                {(busy || loading) && <p>An update or follow-up read is in progress. The observations below may predate it.</p>}
                <p>Formal hold: {status.active_suspension || status.remaining_hold_ids.length > 0 ? "Active" : "None active"}. Account state at that read: {status.effective_status ?? "Unverified"}.</p>
                {!status.history_complete && <p role="status">Only the first {status.history_limit} history records are loaded. This is not a complete account history.</p>}
                {events.length ? <div className="space-y-2">
                    <label htmlFor={`${id}-event`}>Reviewed hold</label>
                    <select id={`${id}-event`} className={control} value={eventId} disabled={busy || Boolean(unknown)} onChange={event => { invalidateDraft(); setEventId(event.target.value); setMode(null); }}>
                        {events.map(event => event && <option key={event.suspension_id} value={event.suspension_id}>{event.is_active ? "Active" : event.restored_at ? "Restored" : "Inactive (restoration unverified)"} · {event.suspension_type} · {event.suspension_id}</option>)}
                    </select>
                    {selected && <div className="text-sm"><p>{selected.reason_description}</p><p>Started: {selected.suspended_at}{selected.suspended_until ? ` · Expires: ${selected.suspended_until}` : ""}</p><p>Appeal: {selected.appeal_decision ?? "Not submitted"}</p>{selected.appeal_reason && <p>Appeal reason: {selected.appeal_reason}</p>}{selected.appeal_response && <p>Review response: {selected.appeal_response}</p>}</div>}
                </div> : <p>No formal holds were returned by this read.</p>}
                <div ref={actionControls} className="flex flex-wrap gap-2">
                    {!status.active_suspension && <Button disabled={disabled || createBlocked} onClick={() => choose("suspend")}>Create formal hold</Button>}
                    {selected?.is_active && <Button variant="outline" disabled={disabled || retired} onClick={() => choose("restore")}>Restore selected hold</Button>}
                    {selected?.is_active && selected.appeal_decision === null && <Button variant="outline" disabled={disabled || retired || completed.includes(`${eventId}:appeal`)} onClick={() => choose("appeal")}>Record operator appeal</Button>}
                    {selected?.is_active && selected.appeal_decision === "pending" && <><Button variant="outline" disabled={disabled || retired || completed.includes(`${eventId}:denied`)} onClick={() => choose("granted")}>Grant appeal</Button><Button variant="outline" disabled={disabled || retired || completed.includes(`${eventId}:denied`)} onClick={() => choose("denied")}>Deny appeal</Button></>}
                </div>
                {retired && selected?.is_active && <p role="status">Restoration of this hold is already confirmed. The displayed history has not caught up; another restore will not be sent.</p>}
            </>}
            {mode && <form ref={draftForm} className="space-y-3 rounded-lg border border-border p-4" onSubmit={event => { event.preventDefault(); void submit(); }}>
                <p className="font-medium">{labels[mode]}</p><p className="break-all text-sm">Partner {partnerId}{mode !== "suspend" ? ` · Hold ${eventId}` : " · Applies to this partner and its affected tenants"}</p>
                {mode === "suspend" && <>
                    <label htmlFor={`${id}-type`}>Hold type</label><select id={`${id}-type`} className={control} value={holdType} disabled={busy} onChange={event => { invalidateDraft(); setHoldType(event.target.value as HoldType); }}>{holdTypes.map(type => <option key={type}>{type}</option>)}</select>
                    <label htmlFor={`${id}-category`}>Reason category</label><input id={`${id}-category`} className={control} value={category} maxLength={100} disabled={busy} onChange={event => { invalidateDraft(); setCategory(event.target.value); }} required/>
                    {(holdType === "TEMPORARY" || holdType === "BILLING") && <><label htmlFor={`${id}-hours`}>Duration in hours</label><input id={`${id}-hours`} className={control} type="number" min={1} step={1} value={hours} disabled={busy} onChange={event => { invalidateDraft(); setHours(event.target.value); }} required/></>}
                    <p className="text-sm">Partner holds restrict new work and request existing activity to stop. Enforcement evidence is shown separately.</p>
                </>}
                <label htmlFor={`${id}-reason`}>{mode === "granted" || mode === "denied" ? "Review response" : "Reason"}</label><textarea id={`${id}-reason`} className={control} value={reason} maxLength={4000} disabled={busy} onChange={event => { invalidateDraft(); setReason(event.target.value); }} required/>
                <label className="flex items-center gap-2"><input type="checkbox" checked={reviewed} disabled={busy} onChange={event => setReviewed(event.target.checked)}/>I reviewed this partner, selected hold and requested change.</label>
                <Button ref={submitButton} type="submit" disabled={disabled || !reviewed || !reason.trim()}>{busy ? "Saving…" : `Confirm: ${labels[mode]}`}</Button>
            </form>}
            {eventId && <section className="space-y-2 border-t border-border pt-4">
                <p className="font-medium">Enforcement observation</p>
                {evidenceError && <p role="alert">Evidence unavailable: {evidenceError}</p>}
                {!evidence && !evidenceError && <p>Loading evidence…</p>}
                {evidence && <><p>{evidence.propagation_status} · observed {evidence.observed_at_db}</p>
                    <p>Selected hold at observation: {evidence.event_state} · Policy cutoff: {evidence.policy_cutoff ?? "Unverified"}</p>
                    <p>Access at observation: {evidence.current_authority.status} · {evidence.current_authority.remaining_hold_ids.length} active hold(s).</p>
                    {evidence.dimensions.map(dimension => <div key={dimension.key} className="rounded-lg border border-border p-3 text-sm"><p className="font-medium">{dimension.key.replaceAll("_"," ")}: {dimension.status.replaceAll("_"," ")}</p><p>Coverage: {dimension.coverage} · Captured {dimension.counts.captured} · Observed {dimension.counts.observed} · Confirmed {dimension.counts.confirmed} · Pending {dimension.counts.pending} · Exempt {dimension.counts.exempt} · Unknown {dimension.counts.unknown}</p>{dimension.reasons.map(text => <p key={text}>{text}</p>)}</div>)}{evidence.reconciliation_reasons.map(text => <p key={text} className="text-sm">{text}</p>)}</>}
            </section>}
        </div>
    </Modal>;
}
