import { AlertCircle, Loader2, LockKeyhole, PhoneIncoming } from "lucide-react";

import { Button } from "@/components/ui/button";

export function InboundLoadingState({ label = "Loading inbound campaign…" }: { label?: string }) {
    return (
        <div className="content-card flex min-h-64 items-center justify-center" role="status" aria-live="polite" aria-busy="true">
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
                {label}
            </div>
        </div>
    );
}

export function InboundErrorState({ title = "Inbound data is unavailable", message, onRetry }: { title?: string; message: string; onRetry?: () => void }) {
    return (
        <div className="content-card flex min-h-64 flex-col items-center justify-center text-center" role="alert">
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-destructive/10 text-destructive"><AlertCircle className="h-5 w-5" aria-hidden /></span>
            <h2 className="mt-4 text-lg font-semibold text-foreground">{title}</h2>
            <p className="mt-1 max-w-lg text-sm text-muted-foreground">{message}</p>
            {onRetry ? <Button type="button" variant="outline" className="mt-4" onClick={onRetry}>Try again</Button> : null}
        </div>
    );
}

export function InboundPermissionState({ action = "view inbound campaigns" }: { action?: string }) {
    return (
        <div className="content-card flex min-h-64 flex-col items-center justify-center text-center" role="alert">
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-muted text-muted-foreground"><LockKeyhole className="h-5 w-5" aria-hidden /></span>
            <p className="mt-4 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">403 · Permission required</p>
            <h2 className="mt-1 text-lg font-semibold text-foreground">You cannot {action}</h2>
            <p className="mt-1 max-w-lg text-sm text-muted-foreground">Ask a tenant administrator to grant the specific inbound capability. Access is re-checked by the server for every action.</p>
        </div>
    );
}

export function InboundEmptyState({ onCreate }: { onCreate?: React.ReactNode }) {
    return (
        <div className="content-card flex min-h-72 flex-col items-center justify-center text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary"><PhoneIncoming className="h-6 w-6" aria-hidden /></span>
            <h2 className="mt-4 text-lg font-semibold text-foreground">No inbound campaigns yet</h2>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">Create a durable draft, assign a verified number, and let server readiness confirm the route before activation.</p>
            {onCreate ? <div className="mt-4">{onCreate}</div> : null}
        </div>
    );
}
