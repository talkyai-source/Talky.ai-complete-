"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { ViewportDrawer } from "@/components/ui/viewport-drawer";
import { Button } from "@/components/ui/button";
import { ChatMessages } from "@/components/support/chat-messages";
import { TicketForm, TEXTAREA_CLASS } from "@/components/support/ticket-form";
import { useSupportChat } from "@/components/support/use-support-chat";
import { describePageContext } from "@/lib/support-page-context";
import { draftFromEscalation } from "@/lib/support-ticket-draft";
import type { PageContext, SupportConfig } from "@/lib/support-api";
import type { SupportIdentity } from "@/lib/support-session";
import { cn } from "@/lib/utils";

const PANEL_CLASS = cn(
    "border border-border bg-background text-foreground",
    // Full screen below sm: the drawer's inline geometry is overridden.
    "max-sm:inset-0! max-sm:w-auto! max-sm:rounded-none! max-sm:border-0!",
);

function Composer({ onSend, disabled, suggestions }: { onSend: (text: string) => void; disabled: boolean; suggestions: string[] }) {
    const [text, setText] = useState("");
    const submit = () => {
        const value = text.trim();
        if (!value || disabled) return;
        onSend(value);
        setText("");
    };
    return (
        <div className="flex flex-col gap-2">
            {suggestions.length ? (
                <div className="flex flex-wrap gap-1.5" aria-label="Quick topics">
                    {suggestions.map((suggestion) => (
                        <button key={suggestion} type="button" disabled={disabled} onClick={() => onSend(suggestion)}
                            className="rounded-full border border-border bg-background/70 px-3 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
                            {suggestion}
                        </button>
                    ))}
                </div>
            ) : null}
            <form className="flex items-end gap-2" onSubmit={(event) => { event.preventDefault(); submit(); }}>
                <label htmlFor="support-chat-input" className="sr-only">Your message</label>
                <textarea
                    id="support-chat-input"
                    value={text}
                    rows={2}
                    maxLength={2000}
                    disabled={disabled}
                    placeholder="Describe the problem…"
                    className={cn(TEXTAREA_CLASS, "min-h-10 flex-1 resize-none")}
                    onChange={(event) => setText(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); }
                    }}
                />
                <Button type="submit" disabled={disabled || !text.trim()}>Send</Button>
            </form>
        </div>
    );
}

/**
 * The support drawer: a right-side panel, full screen on small screens, that
 * walks the chat machine from greeting to resolution or ticket. It wraps
 * ViewportDrawer, which provides the dialog role, Escape, the Tab trap and
 * focus restore to the control that opened it.
 */
export function SupportDrawer({ config, identity, pageContext }: { config: SupportConfig; identity: SupportIdentity | null; pageContext: () => PageContext }) {
    const chat = useSupportChat({ config, identity, pageContext });
    const router = useRouter();
    const { state, open, draft, attachments, setDraft } = chat;
    const context = describePageContext(pageContext());

    // The form draft is created from the server's escalation the first time the form is shown.
    useEffect(() => {
        if (state.phase === "drafting_ticket" && state.escalation && !draft) setDraft(draftFromEscalation(state.escalation, config));
    }, [state.phase, state.escalation, draft, config, setDraft]);

    const writing = state.pending !== null && state.phase !== "failed";
    const typing = writing && (state.pending?.kind === "start" || state.pending?.kind === "message" || state.pending?.kind === "feedback" || state.pending?.kind === "escalate");
    const ended = state.phase === "submitted";
    const canAskPerson = !ended && state.phase !== "submitting" && state.phase !== "drafting_ticket" && !writing && state.conversationId !== null;
    const failedWrite = state.phase === "failed" ? state.pending?.kind : undefined;

    let body: React.ReactNode;
    if (ended && state.outcome) {
        body = (
            <div className="flex flex-col gap-3 rounded-xl border border-border bg-background/70 p-4" role="status">
                {state.outcome.kind === "solved" ? (
                    <>
                        <p className="text-base font-semibold">Glad that helped.</p>
                        <p className="text-sm text-muted-foreground">This conversation is closed. Open a new one any time.</p>
                    </>
                ) : (
                    <>
                        <p className="text-base font-semibold">{state.outcome.kind === "ticket" ? "Your ticket was created." : "Your message was added."}</p>
                        <p className="text-sm">Ticket <span className="font-mono" data-support-ticket-number>{state.outcome.number}</span>. The team replies here and by email.</p>
                        {/* Closing the drawer unmounts this link synchronously (external store),
                            so the navigation is issued explicitly rather than left to Link. */}
                        <Button asChild size="sm">
                            <Link
                                href={`/support/tickets/${state.outcome.ticketId}`}
                                onClick={(event) => {
                                    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
                                    event.preventDefault();
                                    const href = event.currentTarget.getAttribute("href") ?? "/support/tickets";
                                    chat.closeDrawer();
                                    router.push(href);
                                }}
                            >
                                View ticket
                            </Link>
                        </Button>
                    </>
                )}
                <Button type="button" variant="outline" size="sm" onClick={chat.startOver}>Start a new conversation</Button>
            </div>
        );
    } else if ((state.phase === "drafting_ticket" || state.phase === "submitting" || (state.phase === "failed" && (failedWrite === "create_ticket" || failedWrite === "append_to_ticket"))) && state.escalation && draft) {
        body = (
            <div className="flex flex-col gap-3">
                <div className="rounded-xl border border-border bg-background/70 p-3 text-sm">
                    <p className="font-medium">A person will take this from here.</p>
                    <p className="text-muted-foreground">Review the ticket and send it. The team replies here and by email.</p>
                </div>
                {state.phase === "failed" ? (
                    <div role="alert" className="flex flex-col gap-2 rounded-xl border border-red-500/40 bg-red-500/5 p-3 text-sm">
                        <p>{state.error}</p>
                        <p className="text-muted-foreground">Retrying sends the same request again; it cannot create a second ticket.</p>
                        <div><Button type="button" size="sm" onClick={chat.retry}>Retry</Button></div>
                    </div>
                ) : null}
                <TicketForm
                    config={config}
                    draft={draft}
                    onDraftChange={chat.setDraft}
                    attachments={attachments}
                    onAttachmentsChange={chat.setAttachments}
                    existingTicket={state.escalation.existing_ticket}
                    existingChoice={state.existingChoice}
                    onChooseExisting={chat.chooseExisting}
                    onSubmit={chat.submitTicket}
                    onAppend={chat.appendToExisting}
                    busy={state.phase !== "drafting_ticket"}
                    error={null}
                />
            </div>
        );
    } else {
        body = (
            <div className="flex flex-col gap-3">
                <ChatMessages messages={state.messages} typing={typing} />
                {state.phase === "awaiting_feedback" ? (
                    <div className="flex flex-col gap-2 rounded-xl border border-border bg-background/70 p-3" role="group" aria-labelledby="support-resolution-prompt">
                        <p id="support-resolution-prompt" className="text-sm font-medium">Did this solve your problem?</p>
                        <div className="flex gap-2">
                            <Button type="button" size="sm" disabled={writing} onClick={() => chat.feedback(true)}>Yes</Button>
                            <Button type="button" size="sm" variant="outline" disabled={writing} onClick={() => chat.feedback(false)}>No</Button>
                        </div>
                    </div>
                ) : null}
                {state.phase === "failed" ? (
                    <div role="alert" className="flex flex-col gap-2 rounded-xl border border-red-500/40 bg-red-500/5 p-3 text-sm">
                        <p>{state.error}</p>
                        <div className="flex flex-wrap gap-2">
                            {state.pending ? <Button type="button" size="sm" onClick={chat.retry}>Retry</Button> : null}
                            <Button type="button" size="sm" variant="outline" onClick={chat.draftWithoutBot}>Create a ticket instead</Button>
                        </div>
                    </div>
                ) : null}
                {state.phase === "submitting" ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Sending…</p> : null}
            </div>
        );
    }

    const chatting = state.phase === "chatting" || state.phase === "awaiting_feedback";

    return (
        <ViewportDrawer open={open} onOpenChange={(next) => { if (!next) chat.closeDrawer(); }} side="right" size={440} margin={12} ariaLabel="Support" panelClassName={PANEL_CLASS}>
            <div className="flex h-full min-h-0 flex-col" data-support-drawer>
                <header className="flex items-start gap-2 border-b border-border px-4 py-3">
                    <div className="min-w-0 flex-1">
                        <h2 className="text-base font-semibold">Support</h2>
                        <p className="text-xs text-muted-foreground" data-support-includes>{context}</p>
                    </div>
                    <Button type="button" variant="ghost" size="icon" aria-label="Close support" onClick={chat.closeDrawer}><X className="h-4 w-4" aria-hidden /></Button>
                </header>
                <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2 text-xs">
                    <span className="text-muted-foreground">{config.bot_enabled ? "The assistant answers first." : "A person will reply."}</span>
                    <button type="button" disabled={!canAskPerson} onClick={chat.escalate} className="underline underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:no-underline disabled:opacity-50" data-support-talk-to-person>
                        Talk to a person
                    </button>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{body}</div>
                {chatting || (state.phase === "failed" && (failedWrite === "message" || failedWrite === "start")) ? (
                    <footer className="border-t border-border px-4 py-3">
                        <Composer onSend={chat.send} disabled={writing || state.phase === "failed" || state.conversationId === null} suggestions={state.messages.length <= 1 ? state.suggestions : []} />
                    </footer>
                ) : null}
            </div>
        </ViewportDrawer>
    );
}
