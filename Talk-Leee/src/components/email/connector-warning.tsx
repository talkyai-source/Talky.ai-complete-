"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";

export function ConnectorWarning({
    blocked,
    title,
    message,
    className,
}: {
    blocked: boolean;
    title: string;
    message: string;
    className?: string;
}) {
    if (!blocked) return null;
    return (
        <div className={cn("rounded-2xl border border-amber-500/20 bg-amber-500/10 p-4 text-amber-100", className)}>
            <div className="flex items-start gap-3">
                <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-xl bg-amber-500/15 border border-amber-500/20 shrink-0">
                    <AlertTriangle className="h-4 w-4 text-amber-300" aria-hidden />
                </div>
                <div className="min-w-0">
                    <div className="text-sm font-semibold">{title}</div>
                    <div className="mt-1 text-sm text-amber-100/80">{message}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Link
                            href="/settings/connectors"
                            className="inline-flex items-center justify-center rounded-lg bg-amber-300 px-3 py-1.5 text-xs font-bold text-amber-950 hover:bg-amber-200 transition-colors"
                        >
                            Fix connector
                        </Link>
                        <div className="text-xs text-amber-100/70 self-center">Reconnect email or refresh credentials.</div>
                    </div>
                </div>
            </div>
        </div>
    );
}

