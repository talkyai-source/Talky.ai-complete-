import { cn } from "@/lib/utils";
import type { TicketPriority, TicketStatus } from "@/lib/support-api";

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
    open: "Open",
    in_progress: "In progress",
    waiting_on_customer: "Waiting on you",
    resolved: "Resolved",
    closed: "Closed",
};

export const TICKET_PRIORITY_LABELS: Record<TicketPriority, string> = {
    urgent: "Urgent",
    high: "High",
    normal: "Normal",
    low: "Low",
};

const STATUS_CLASSES: Record<TicketStatus, string> = {
    open: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
    in_progress: "border-indigo-500/40 bg-indigo-500/10 text-indigo-700 dark:text-indigo-300",
    waiting_on_customer: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    resolved: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    closed: "border-border bg-muted text-muted-foreground",
};

const PRIORITY_CLASSES: Record<TicketPriority, string> = {
    urgent: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
    high: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300",
    normal: "border-border bg-background text-foreground/80",
    low: "border-border bg-muted text-muted-foreground",
};

export function ticketStatusLabel(status: string): string {
    return (TICKET_STATUS_LABELS as Record<string, string>)[status] ?? status;
}

export function ticketPriorityLabel(priority: string): string {
    return (TICKET_PRIORITY_LABELS as Record<string, string>)[priority] ?? priority;
}

export function TicketStatusBadge({ status, className }: { status: TicketStatus; className?: string }) {
    return (
        <span className={cn("inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", STATUS_CLASSES[status], className)}>
            {TICKET_STATUS_LABELS[status]}
        </span>
    );
}

export function TicketPriorityBadge({ priority, className }: { priority: TicketPriority; className?: string }) {
    return (
        <span className={cn("inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium", PRIORITY_CLASSES[priority], className)}>
            {TICKET_PRIORITY_LABELS[priority]}
        </span>
    );
}

export function formatTicketDate(value: string): string {
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return value;
    return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(new Date(time));
}
