"use client";

import React from "react";
import type { ComponentType } from "react";
import { CheckCircle2, XCircle, Clock, AlertCircle } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type StatusPillState = "connected" | "disconnected" | "expired" | "error";

export type StatusPillTheme = Partial<
    Record<
        StatusPillState,
        {
            pillClassName: string;
            dotClassName: string;
            icon: ComponentType<{ className?: string }>;
            iconClassName?: string;
            defaultTooltip: string;
            defaultLabel: string;
        }
    >
>;

const DEFAULT_THEME: StatusPillTheme = {
    connected: {
        pillClassName: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700",
        dotClassName: "bg-emerald-500",
        icon: CheckCircle2,
        iconClassName: "text-emerald-600",
        defaultLabel: "Connected",
        defaultTooltip: "This connector is active and syncing successfully.",
    },
    disconnected: {
        pillClassName: "border-gray-300 bg-gray-100 text-gray-700",
        dotClassName: "bg-gray-400",
        icon: XCircle,
        iconClassName: "text-gray-500",
        defaultLabel: "Disconnected",
        defaultTooltip: "This connector is not connected yet.",
    },
    expired: {
        pillClassName: "border-amber-500/30 bg-amber-500/10 text-amber-700",
        dotClassName: "bg-amber-500",
        icon: Clock,
        iconClassName: "text-amber-600",
        defaultLabel: "Expired",
        defaultTooltip: "Credentials have expired. Reconnect to refresh access.",
    },
    error: {
        pillClassName: "border-red-500/30 bg-red-500/10 text-red-700",
        dotClassName: "bg-red-500",
        icon: AlertCircle,
        iconClassName: "text-red-600",
        defaultLabel: "Error",
        defaultTooltip: "The connector encountered an error. Reconnect or check details.",
    },
};

export function StatusPill({
    state,
    label,
    tooltip,
    tooltipDelayMs = 120,
    size = "md",
    showDot = true,
    showIcon = true,
    theme,
    className,
}: {
    state: StatusPillState;
    label?: string;
    tooltip?: string;
    tooltipDelayMs?: number;
    size?: "sm" | "md";
    showDot?: boolean;
    showIcon?: boolean;
    theme?: StatusPillTheme;
    className?: string;
}) {
    const t = { ...DEFAULT_THEME, ...(theme ?? {}) }[state] ?? DEFAULT_THEME.disconnected!;
    const Icon = t.icon;
    const resolvedLabel = label ?? t.defaultLabel;
    const resolvedTooltip = tooltip ?? t.defaultTooltip;

    const pill = (
        <span
            className={cn(
                "inline-flex items-center gap-2 rounded-xl border font-semibold",
                size === "sm" ? "px-2.5 py-1 text-[11px]" : "px-3 py-2 text-xs",
                t.pillClassName,
                className
            )}
            aria-label={`Status: ${resolvedLabel}`}
            tabIndex={0}
        >
            {showDot ? <span className={cn("h-2 w-2 rounded-full", t.dotClassName)} aria-hidden /> : null}
            {showIcon ? <Icon className={cn("h-4 w-4", t.iconClassName)} aria-hidden /> : null}
            <span>{resolvedLabel}</span>
        </span>
    );

    return (
        <TooltipProvider delayDuration={Math.max(0, tooltipDelayMs)}>
            <Tooltip>
                <TooltipTrigger asChild>{pill}</TooltipTrigger>
                <TooltipContent showArrow className="max-w-[320px]">
                    {resolvedTooltip}
                </TooltipContent>
            </Tooltip>
        </TooltipProvider>
    );
}
