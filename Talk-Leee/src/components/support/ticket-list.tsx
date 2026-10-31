"use client";

import Link from "next/link";
import { formatTicketDate, TicketPriorityBadge, TicketStatusBadge } from "@/components/support/ticket-status-badge";
import type { TicketListItem } from "@/lib/support-api";
import { cn } from "@/lib/utils";

/**
 * Tickets as rows: number, subject, status, priority, last update. Each row is
 * one link, so the keyboard reaches every ticket with Tab and opens it with
 * Enter. Below sm the columns stack so nothing overflows at 360px.
 */
export function TicketList({ items, emptyText = "No tickets here.", className }: { items: TicketListItem[]; emptyText?: string; className?: string }) {
    if (!items.length) return <p className={cn("text-sm text-muted-foreground", className)}>{emptyText}</p>;
    return (
        <ul className={cn("flex flex-col gap-2", className)} aria-label="Tickets" data-support-ticket-list>
            {items.map((item) => (
                <li key={item.id}>
                    <Link
                        href={`/support/tickets/${item.id}`}
                        className={cn(
                            "grid min-w-0 gap-x-3 gap-y-1 rounded-xl border border-border bg-background/70 px-3 py-2 text-sm transition-colors hover:bg-accent/40",
                            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            "grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[7.5rem_minmax(0,1fr)_auto_auto_9.5rem] sm:items-center",
                        )}
                        aria-label={`${item.number}: ${item.subject}${item.unread ? ", unread reply" : ""}`}
                    >
                        <span className="flex items-center gap-1.5 font-mono text-xs text-muted-foreground">
                            {item.unread ? <span aria-hidden className="h-2 w-2 rounded-full bg-red-500" /> : null}
                            {item.number}
                        </span>
                        <span className="col-span-2 min-w-0 truncate font-medium sm:col-span-1" title={item.subject}>{item.subject}</span>
                        <TicketStatusBadge status={item.status} className="justify-self-start" />
                        <TicketPriorityBadge priority={item.priority} className="justify-self-start" />
                        <span className="col-span-2 text-xs text-muted-foreground sm:col-span-1 sm:text-right">
                            <span className="sr-only">Last update </span>
                            {formatTicketDate(item.updated_at)}
                        </span>
                    </Link>
                </li>
            ))}
        </ul>
    );
}
