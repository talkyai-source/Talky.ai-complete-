"use client";

import { Paperclip } from "lucide-react";
import { formatTicketDate } from "@/components/support/ticket-status-badge";
import { apiBaseUrl } from "@/lib/env";
import { supportApi, type TicketMessage } from "@/lib/support-api";
import { formatBytes } from "@/lib/support-attachments";
import { cn } from "@/lib/utils";

const KNOWN_AUTHORS = new Set(["customer", "staff", "system"]);

/** Messages the thread shows: customer, staff and system; any other author type is ignored. */
export function visibleTicketMessages(messages: TicketMessage[]): TicketMessage[] {
    return messages.filter((message) => KNOWN_AUTHORS.has(message.author_type));
}

function authorLabel(message: TicketMessage): string {
    if (message.author_type === "staff") return message.author_name || "Support";
    if (message.author_type === "system") return message.author_name || "System";
    return message.author_name || "You";
}

/**
 * The ticket's message thread. Attachment links go to the API route that
 * answers with a redirect to a short-lived signed link; the browser follows
 * it as a navigation with the session cookie.
 */
export function TicketThread({ messages, className }: { messages: TicketMessage[]; className?: string }) {
    const visible = visibleTicketMessages(messages);
    if (!visible.length) return <p className={cn("text-sm text-muted-foreground", className)}>No messages yet.</p>;
    return (
        <ol className={cn("flex flex-col gap-3", className)} aria-label="Messages" data-support-thread>
            {visible.map((message) => (
                <li
                    key={message.id}
                    data-author-type={message.author_type}
                    className={cn(
                        "flex min-w-0 flex-col gap-1 rounded-xl border px-3 py-2 text-sm",
                        message.author_type === "staff" ? "border-sky-500/30 bg-sky-500/5" : message.author_type === "system" ? "border-dashed border-border bg-muted/40" : "border-border bg-background/70",
                    )}
                >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                        <span className="font-medium">{authorLabel(message)}</span>
                        <time dateTime={message.created_at} className="text-xs text-muted-foreground">{formatTicketDate(message.created_at)}</time>
                    </div>
                    <p className="whitespace-pre-wrap break-words">{message.body}</p>
                    {message.attachments.length ? (
                        <ul className="mt-1 flex flex-wrap gap-1.5" aria-label="Attachments">
                            {message.attachments.map((attachment) => (
                                // min-w-0 on the item and the name: a flex item defaults to
                                // min-width:auto, which let a long filename push the chip past
                                // a 320px viewport instead of truncating.
                                <li key={attachment.id} className="min-w-0 max-w-full">
                                    <a
                                        href={`${apiBaseUrl()}${supportApi.attachmentPath(attachment.id)}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="flex max-w-full min-w-0 items-center gap-1 rounded-full border border-border bg-background/70 px-2.5 py-0.5 text-xs underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                    >
                                        <Paperclip className="h-3 w-3 shrink-0" aria-hidden />
                                        <span className="min-w-0 truncate">{attachment.filename}</span>
                                        <span className="shrink-0 text-muted-foreground">{formatBytes(attachment.size_bytes)}</span>
                                    </a>
                                </li>
                            ))}
                        </ul>
                    ) : null}
                </li>
            ))}
        </ol>
    );
}
