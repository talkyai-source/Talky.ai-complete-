"use client";

import React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";

export type ConfirmDialogIntent = "disconnect" | "cancel" | "delete";

function defaultsForIntent(intent: ConfirmDialogIntent) {
    if (intent === "cancel") {
        return {
            title: "Cancel action",
            description: "This will stop the current operation.",
            confirmLabel: "Cancel",
        };
    }
    if (intent === "delete") {
        return {
            title: "Delete item",
            description: "This action cannot be undone.",
            confirmLabel: "Delete",
        };
    }
    return {
        title: "Disconnect",
        description: "This will stop syncing and revoke access for this connection.",
        confirmLabel: "Disconnect",
    };
}

export function ConfirmDialog({
    open,
    onOpenChange,
    intent = "disconnect",
    title,
    description,
    warningText,
    confirmLabel,
    cancelLabel = "Cancel",
    pendingLabel,
    onConfirm,
    onCancel,
    onError,
    confirmDisabled,
    className,
}: {
    open: boolean;
    onOpenChange: (next: boolean) => void;
    intent?: ConfirmDialogIntent;
    title?: string;
    description?: string;
    warningText: string;
    confirmLabel?: string;
    cancelLabel?: string;
    pendingLabel?: string;
    onConfirm: () => void | Promise<void>;
    onCancel?: () => void;
    onError?: (err: unknown) => void;
    confirmDisabled?: boolean;
    className?: string;
}) {
    const [pending, setPending] = useState(false);
    const [inlineError, setInlineError] = useState<string | undefined>(undefined);
    const cancelRef = useRef<HTMLButtonElement | null>(null);

    const defaults = useMemo(() => defaultsForIntent(intent), [intent]);
    const resolvedTitle = title ?? defaults.title;
    const resolvedDescription = description ?? defaults.description;
    const resolvedConfirmLabel = confirmLabel ?? defaults.confirmLabel;
    const resolvedPendingLabel = pendingLabel ?? `${resolvedConfirmLabel}...`;

    useEffect(() => {
        if (!open) {
            setPending(false);
            setInlineError(undefined);
        }
    }, [open]);

    return (
        <Modal
            open={open}
            onOpenChange={onOpenChange}
            title={resolvedTitle}
            description={resolvedDescription}
            ariaLabel={resolvedTitle}
            initialFocusRef={cancelRef}
            trapFocus
            footer={
                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <Button
                        ref={cancelRef}
                        type="button"
                        variant="ghost"
                        onClick={() => {
                            setInlineError(undefined);
                            onCancel?.();
                            onOpenChange(false);
                        }}
                        disabled={pending}
                    >
                        {cancelLabel}
                    </Button>
                    <Button
                        type="button"
                        variant="destructive"
                        onClick={() => {
                            if (pending) return;
                            setPending(true);
                            setInlineError(undefined);
                            let p: Promise<void>;
                            try {
                                p = Promise.resolve(onConfirm());
                            } catch (err) {
                                p = Promise.reject(err);
                            }
                            p
                                .then(() => onOpenChange(false))
                                .catch((err) => {
                                    const msg = err instanceof Error ? err.message : "Action failed. Please try again.";
                                    setInlineError(msg);
                                    onError?.(err);
                                })
                                .finally(() => setPending(false));
                        }}
                        disabled={pending || Boolean(confirmDisabled)}
                    >
                        {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                        {pending ? resolvedPendingLabel : resolvedConfirmLabel}
                    </Button>
                </div>
            }
        >
            <div className={cn("flex items-start gap-3", className)}>
                <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-xl border border-red-500/20 bg-red-500/10 text-red-300 shrink-0">
                    <AlertTriangle className="h-4 w-4" aria-hidden />
                </div>
                <div className="min-w-0">
                    <div className="text-sm font-semibold text-foreground">Warning</div>
                    <div className="mt-1 text-sm text-muted-foreground" role="alert">
                        {warningText}
                    </div>
                    <div className="mt-2 text-xs text-muted-foreground">Review the details, then confirm to proceed.</div>
                    {inlineError ? (
                        <div className="mt-3 rounded-xl border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-100" role="alert">
                            {inlineError}
                        </div>
                    ) : null}
                </div>
            </div>
        </Modal>
    );
}
