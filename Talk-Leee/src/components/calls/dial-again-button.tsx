"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, PhoneOutgoing } from "lucide-react";
import { api } from "@/lib/api";
import type { Call } from "@/lib/dashboard-api";
import { isActiveCallStatus } from "@/lib/call-history-workflow";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";

export interface RedialPreview {
    eligible: boolean;
    reason?: string | null;
    reason_code?: string | null;
    caller_id?: string | null;
    trunk_id?: string | null;
    campaign_id: string;
    lead_id: string;
    phone_number: string;
    job_id?: string | null;
}

interface RedialReceipt {
    status: string;
    job_id: string;
    campaign_id: string;
    lead_id: string;
    phone_number: string;
    message: string;
}

export function DialAgainButton({ call, compact = false }: { call: Call; compact?: boolean }) {
    const [open, setOpen] = useState(false);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [receipt, setReceipt] = useState<RedialReceipt | null>(null);
    const submitting = useRef(false);
    const queryClient = useQueryClient();
    const path = `/calls/${encodeURIComponent(call.id)}/redial`;
    const retrySavedRequest = receipt?.status === "pending";
    const preview = useQuery({
        queryKey: ["calls", call.id, "redial"],
        queryFn: () => api.request<RedialPreview>({ path }),
        enabled: open && !receipt,
        refetchInterval: open && !receipt ? 10000 : false,
        retry: false,
    });

    // The server owns DNC, campaign, current route, and duplicate-job checks.
    // Do not initiate previews for inbound or ongoing calls.
    if (call.direction === "inbound" || isActiveCallStatus(call.status) || !call.campaign_id || !call.lead_id) return null;

    async function dial() {
        if (submitting.current || (!retrySavedRequest && (!preview.data?.eligible || preview.isFetching || receipt))) return;
        submitting.current = true;
        setPending(true);
        setError(null);
        try {
            const result = await api.request<RedialReceipt>({ path, method: "POST" });
            setReceipt(result);
            void queryClient.invalidateQueries({ queryKey: ["calls"] });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Could not request another call. Please check the call status before retrying.");
            void preview.refetch();
        } finally {
            submitting.current = false;
            setPending(false);
        }
    }

    return <>
        <button type="button" onClick={() => setOpen(true)} aria-label={`Dial again ${call.phone_number}`} title="Dial again"
            className={compact ? "inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border text-muted-foreground hover:bg-accent" : "inline-flex h-11 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium text-muted-foreground hover:bg-accent"}>
            <PhoneOutgoing className="h-4 w-4" aria-hidden />{!compact && "Dial again"}
        </button>
        <Modal open={open} onOpenChange={(next) => { if (!pending) setOpen(next); }} title="Dial again" description="Request one new call for this contact in the original campaign."
            footer={<div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setOpen(false)} disabled={pending}>Close</Button>{(!receipt || retrySavedRequest) && <Button onClick={() => void dial()} disabled={pending || (!retrySavedRequest && (preview.isFetching || preview.isError || !preview.data?.eligible))}>{pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <PhoneOutgoing className="h-4 w-4" aria-hidden />}{pending ? "Requesting…" : retrySavedRequest ? "Retry saved request" : "Call again"}</Button>}</div>}>
            <div className="space-y-3 text-sm">
                <p>To: <strong>{receipt?.phone_number || preview.data?.phone_number || call.phone_number}</strong></p>
                <p>Caller ID at last check: <strong>{preview.data?.caller_id || "Waiting for an available caller ID"}</strong></p>
                {receipt ? <p role="status">{receipt.message || `Call request ${receipt.status}.`} This is a queue receipt; the call has not necessarily connected.</p> : <>
                    {preview.isFetching && <p role="status">Checking the campaign, contact and current calling route…</p>}
                    {preview.isError && <div role="alert">{preview.error instanceof Error ? preview.error.message : "Readiness could not be checked."} <Button variant="outline" size="sm" onClick={() => void preview.refetch()}>Check again</Button></div>}
                    {preview.data && <p role="status">{preview.data.eligible ? "Ready to request a call using the current campaign route." : preview.data.reason || "This call is not eligible for another attempt."}</p>}
                </>}
                {retrySavedRequest && <p>The request is saved, but its queue handoff is unconfirmed. Retry reuses this saved request.</p>}
                {error && <p role="alert" className="text-destructive">{error}</p>}
            </div>
        </Modal>
    </>;
}
