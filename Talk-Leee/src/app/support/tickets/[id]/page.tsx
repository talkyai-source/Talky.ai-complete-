"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useId, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, LoadingState } from "@/components/states/page-states";
import { AttachmentPicker } from "@/components/support/attachment-picker";
import { RatingCard } from "@/components/support/rating-card";
import { SupportPageGate } from "@/components/support/support-unavailable";
import { formatTicketDate, TicketPriorityBadge, TicketStatusBadge } from "@/components/support/ticket-status-badge";
import { TEXTAREA_CLASS } from "@/components/support/ticket-form";
import { TicketThread } from "@/components/support/ticket-thread";
import { hasUploadInFlight, uploadedAttachmentIds, type PickedAttachment } from "@/lib/support-attachments";
import { describeSupportError, isSupportUnavailable, newRequestId, supportApi, type SupportConfig, type Ticket } from "@/lib/support-api";
import { useCommitTicket, useTicket } from "@/lib/support-ticket-queries";
import { cn } from "@/lib/utils";

type ActionKind = "reply" | "resolve" | "reopen" | "rate";
/** One write at a time; a failed one keeps its request_id so Retry sends the same request. */
type ActionState = { kind: ActionKind; requestId: string; run: () => Promise<void>; busy: boolean; error: string | null };

const UNCONFIRMED = "The change could not be confirmed. The ticket below shows what the server saved; Retry sends the same request again.";

function TicketView({ ticket, config, refetch }: { ticket: Ticket; config: SupportConfig; refetch: () => Promise<unknown> }) {
    const id = useId();
    const commit = useCommitTicket();
    const [action, setAction] = useState<ActionState | null>(null);
    const [reply, setReply] = useState("");
    const [attachments, setAttachments] = useState<PickedAttachment[]>([]);
    const can = (kind: "reply" | "resolve" | "reopen" | "rate") => ticket.allowed_actions.includes(kind);
    const categoryLabel = config.categories.find((category) => category.id === ticket.category)?.label ?? ticket.category;

    const start = useCallback((kind: ActionKind, requestId: string, write: (requestId: string) => Promise<void>) => {
        const run = async () => {
            setAction((current) => current && { ...current, busy: true, error: null });
            try {
                await write(requestId);
                setAction(null);
            } catch (error) {
                // Re-read what the server holds before offering the same request again.
                await refetch().catch(() => undefined);
                const message = isSupportUnavailable(error) ? describeSupportError(error) : `${describeSupportError(error)} ${UNCONFIRMED}`;
                setAction((current) => current && { ...current, busy: false, error: message });
            }
        };
        setAction({ kind, requestId, run, busy: false, error: null });
        void run();
    }, [refetch]);

    const sendReply = () => {
        const body = reply.trim();
        if (!body || action) return;
        start("reply", newRequestId(), async (requestId) => {
            await supportApi.replyToTicket(ticket.id, requestId, body, uploadedAttachmentIds(attachments));
            setReply("");
            setAttachments([]);
            await refetch();
        });
    };
    const transition = (kind: "resolve" | "reopen") => {
        if (action) return;
        start(kind, newRequestId(), async (requestId) => {
            const result = kind === "resolve" ? await supportApi.resolveTicket(ticket.id, requestId) : await supportApi.reopenTicket(ticket.id, requestId);
            commit(result.data);
        });
    };
    const rate = (score: number, comment: string) => {
        if (action) return;
        start("rate", newRequestId(), async (requestId) => {
            const result = await supportApi.rateTicket(ticket.id, requestId, score, comment || undefined);
            commit(result.data);
        });
    };
    const busy = action?.busy === true;

    return (
        <div className="flex flex-col gap-4" data-support-ticket={ticket.id}>
            <Card>
                <CardHeader className="gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-sm text-muted-foreground" data-support-ticket-number>{ticket.number}</span>
                        <TicketStatusBadge status={ticket.status} />
                        <TicketPriorityBadge priority={ticket.priority} />
                    </div>
                    <CardTitle className="break-words">{ticket.subject}</CardTitle>
                </CardHeader>
                <CardContent>
                    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                        <div><dt className="text-muted-foreground">Category</dt><dd>{categoryLabel}</dd></div>
                        <div><dt className="text-muted-foreground">Opened by</dt><dd>{ticket.requester.name ?? "—"}</dd></div>
                        <div><dt className="text-muted-foreground">Created</dt><dd><time dateTime={ticket.created_at}>{formatTicketDate(ticket.created_at)}</time></dd></div>
                        <div><dt className="text-muted-foreground">Last update</dt><dd><time dateTime={ticket.updated_at}>{formatTicketDate(ticket.updated_at)}</time></dd></div>
                        {ticket.related.campaign_id ? <div><dt className="text-muted-foreground">Related campaign</dt><dd><Link href={`/campaigns/${ticket.related.campaign_id}`} className="underline underline-offset-2">Open the campaign</Link></dd></div> : null}
                        {ticket.related.call_id ? <div><dt className="text-muted-foreground">Related call</dt><dd><Link href={`/calls/${ticket.related.call_id}`} className="underline underline-offset-2">Open the call</Link></dd></div> : null}
                        {ticket.csat ? <div><dt className="text-muted-foreground">Your rating</dt><dd>{ticket.csat.score} / 5{ticket.csat.comment ? ` · ${ticket.csat.comment}` : ""}</dd></div> : null}
                    </dl>
                    {can("resolve") || can("reopen") ? (
                        <div className="mt-4 flex flex-wrap gap-2">
                            {can("resolve") ? <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => transition("resolve")}>Mark as resolved</Button> : null}
                            {can("reopen") ? <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => transition("reopen")}>Reopen</Button> : null}
                        </div>
                    ) : null}
                </CardContent>
            </Card>

            {action?.error ? (
                <div role="alert" className="flex flex-col gap-2 rounded-xl border border-red-500/40 bg-red-500/5 p-3 text-sm">
                    <p>{action.error}</p>
                    <div className="flex gap-2">
                        <Button type="button" size="sm" disabled={busy} onClick={() => void action.run()}>Retry</Button>
                        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setAction(null)}>Dismiss</Button>
                    </div>
                </div>
            ) : null}

            <Card>
                <CardHeader><CardTitle>Messages</CardTitle></CardHeader>
                <CardContent><TicketThread messages={ticket.messages} /></CardContent>
            </Card>

            {can("reply") ? (
                <Card>
                    <CardHeader><CardTitle>Reply</CardTitle></CardHeader>
                    <CardContent>
                        <form className="flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); sendReply(); }}>
                            <label htmlFor={`${id}-reply`} className="sr-only">Your reply</label>
                            <textarea id={`${id}-reply`} rows={4} maxLength={5000} value={reply} disabled={busy} placeholder="Write your reply…" className={cn(TEXTAREA_CLASS, "resize-y")} onChange={(event) => setReply(event.target.value)} />
                            <AttachmentPicker items={attachments} onChange={(update) => setAttachments(update)} limits={config.attachments} disabled={busy} />
                            <div><Button type="submit" disabled={busy || !reply.trim() || hasUploadInFlight(attachments) || action !== null}>Send reply</Button></div>
                        </form>
                    </CardContent>
                </Card>
            ) : (
                <p className="text-sm text-muted-foreground">
                    {ticket.status === "closed" ? "This ticket is closed. Open a new ticket for anything further." : "Replies are not available on this ticket."}
                </p>
            )}

            {can("rate") && (ticket.status === "resolved" || ticket.status === "closed") && !ticket.csat ? (
                <RatingCard onSubmit={rate} busy={busy} error={action?.kind === "rate" ? action.error : null} />
            ) : null}
        </div>
    );
}

function TicketLoader({ ticketId, config }: { ticketId: string; config: SupportConfig }) {
    const query = useTicket(ticketId, true);
    if (query.isPending) return <LoadingState title="Loading ticket" />;
    if (query.error || !query.data) {
        return <ErrorState title="This ticket could not be loaded" message={query.error ? describeSupportError(query.error) : "Retry in a moment."} onRetry={() => void query.refetch()} actionHref="/support/tickets" actionLabel="Back to tickets" />;
    }
    return <TicketView ticket={query.data} config={config} refetch={query.refetch} />;
}

export default function SupportTicketPage() {
    const params = useParams<{ id: string }>();
    const ticketId = typeof params?.id === "string" ? params.id : null;
    return (
        <DashboardLayout title="Support ticket">
            <div className="mx-auto flex w-full max-w-5xl flex-col gap-3">
                <div><Button asChild variant="ghost" size="sm"><Link href="/support/tickets">← All tickets</Link></Button></div>
                <SupportPageGate title="Loading ticket">
                    {(gate) => (ticketId ? <TicketLoader ticketId={ticketId} config={gate.config} /> : <ErrorState title="No ticket selected" message="Choose a ticket from the list." actionHref="/support/tickets" actionLabel="Back to tickets" />)}
                </SupportPageGate>
            </div>
        </DashboardLayout>
    );
}
