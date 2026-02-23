"use client";

import { useHealth } from "@/lib/api-hooks";
import { cn } from "@/lib/utils";

type Variant = "ok" | "degraded" | "down";

function meta(variant: Variant) {
    if (variant === "ok") return { label: "Healthy", dot: "bg-emerald-500", text: "text-emerald-700", ring: "ring-emerald-500/20" };
    if (variant === "degraded") return { label: "Degraded", dot: "bg-amber-500", text: "text-amber-700", ring: "ring-amber-500/20" };
    return { label: "Down", dot: "bg-red-500", text: "text-red-700", ring: "ring-red-500/20" };
}

export function HealthIndicator({ className }: { className?: string }) {
    const q = useHealth();
    const variant: Variant = q.isError ? "down" : q.isFetching ? "degraded" : q.data?.status === "ok" ? "ok" : "degraded";
    const m = meta(variant);

    return (
        <div
            className={cn(
                "inline-flex items-center gap-2 rounded-xl border border-border bg-background/70 px-3 py-2 text-xs font-semibold ring-1 backdrop-blur-sm",
                m.ring,
                className
            )}
            aria-label={`Health: ${m.label}`}
            title={`Health: ${m.label}`}
        >
            <span className={cn("h-2 w-2 rounded-full", m.dot)} aria-hidden />
            <span className={cn(m.text)}>{m.label}</span>
        </div>
    );
}

