import { AlertCircle, LockKeyhole } from "lucide-react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states/page-states";

export function InboundLoadingState({ label = "Loading inbound campaign…" }: { label?: string }) {
    return <LoadingState title={label} />;
}

export function InboundErrorState({ title = "Inbound data is unavailable", message, onRetry }: { title?: string; message: string; onRetry?: () => void }) {
    // A failed read: the shared ErrorState with Retry. Without a retry this is
    // a notice about the campaign's state, not a failed read.
    if (onRetry) return <ErrorState title={title} message={message} onRetry={onRetry} />;
    return (
        <div className="content-card flex min-h-64 flex-col items-center justify-center text-center" role="alert">
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-destructive/10 text-destructive"><AlertCircle className="h-5 w-5" aria-hidden /></span>
            <h2 className="mt-4 text-lg font-semibold text-foreground">{title}</h2>
            <p className="mt-1 max-w-lg text-sm text-muted-foreground">{message}</p>
        </div>
    );
}

export function InboundPermissionState({ action = "view inbound campaigns", onRetry }: { action?: string; onRetry?: () => void }) {
    // A permission refusal is a failed read: the shared ErrorState with Retry.
    if (onRetry) {
        return (
            <ErrorState
                title={`You cannot ${action}`}
                message="Ask a tenant administrator to grant the specific inbound capability. Access is re-checked by the server for every action."
                onRetry={onRetry}
            />
        );
    }
    return (
        <div className="content-card flex min-h-64 flex-col items-center justify-center text-center" role="alert">
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground"><LockKeyhole className="h-5 w-5" aria-hidden /></span>
            <p className="mt-4 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">403 · Permission required</p>
            <h2 className="mt-1 text-lg font-semibold text-foreground">You cannot {action}</h2>
            <p className="mt-1 max-w-lg text-sm text-muted-foreground">Ask a tenant administrator to grant the specific inbound capability. Access is re-checked by the server for every action.</p>
        </div>
    );
}

export function InboundEmptyState({ createHref }: { createHref?: string }) {
    return (
        <EmptyState
            title="No inbound campaigns yet"
            message="Create a durable draft, assign a verified number, and let server readiness confirm the route before activation."
            actionLabel={createHref ? "Create the first draft" : undefined}
            actionHref={createHref}
        />
    );
}
