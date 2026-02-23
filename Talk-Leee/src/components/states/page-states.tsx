"use client";

import React from "react";
import Link from "next/link";
import type { ComponentType } from "react";
import {
    CircleAlert,
    Inbox,
    Mail,
    MessageCircleWarning,
    Plus,
    RotateCcw,
    Search,
    Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

function SkeletonBlock({
    className,
    rounded = "md",
}: {
    className?: string;
    rounded?: "sm" | "md" | "lg" | "full";
}) {
    const roundedClass =
        rounded === "full" ? "rounded-full" : rounded === "lg" ? "rounded-xl" : rounded === "sm" ? "rounded" : "rounded-lg";
    return <div className={cn("animate-pulse bg-foreground/10", roundedClass, className)} />;
}

export function LoadingSkeleton({
    variant = "text",
    lines = 3,
    items = 4,
    showAction = true,
    lineWidths,
    className,
}: {
    variant?: "text" | "list" | "card" | "table" | "form";
    lines?: number;
    items?: number;
    showAction?: boolean;
    lineWidths?: string[];
    className?: string;
}) {
    const resolvedLines = Math.max(1, lines);
    const resolvedItems = Math.max(1, items);
    const widths =
        lineWidths && lineWidths.length > 0
            ? lineWidths
            : Array.from({ length: resolvedLines }).map((_, i) => (i === 0 ? "w-2/3" : i === 1 ? "w-1/2" : "w-3/5"));

    return (
        <div className={cn("space-y-3", className)} aria-busy="true" aria-live="polite">
            {variant === "text" ? (
                <>
                    {Array.from({ length: resolvedLines }).map((_, i) => (
                        <SkeletonBlock key={i} className={cn("h-4", widths[i] ?? widths[widths.length - 1]!)} />
                    ))}
                    {showAction ? <SkeletonBlock className="h-10 w-32" rounded="lg" /> : null}
                </>
            ) : null}

            {variant === "list" ? (
                <>
                    {Array.from({ length: resolvedItems }).map((_, row) => (
                        <div key={row} className="flex items-center gap-3">
                            <SkeletonBlock className="h-10 w-10" rounded="full" />
                            <div className="min-w-0 flex-1 space-y-2">
                                <SkeletonBlock className={cn("h-4", row % 2 === 0 ? "w-2/3" : "w-1/2")} />
                                <SkeletonBlock className={cn("h-3", row % 3 === 0 ? "w-3/5" : "w-2/5")} />
                            </div>
                        </div>
                    ))}
                    {showAction ? <SkeletonBlock className="h-10 w-36" rounded="lg" /> : null}
                </>
            ) : null}

            {variant === "card" ? (
                <>
                    <SkeletonBlock className="h-28 w-full" rounded="lg" />
                    {Array.from({ length: resolvedLines }).map((_, i) => (
                        <SkeletonBlock key={i} className={cn("h-4", widths[i] ?? widths[widths.length - 1]!)} />
                    ))}
                    {showAction ? <SkeletonBlock className="h-10 w-40" rounded="lg" /> : null}
                </>
            ) : null}

            {variant === "table" ? (
                <>
                    <div className="grid grid-cols-3 gap-3">
                        <SkeletonBlock className="h-4 w-full" />
                        <SkeletonBlock className="h-4 w-full" />
                        <SkeletonBlock className="h-4 w-full" />
                    </div>
                    <div className="space-y-2">
                        {Array.from({ length: resolvedItems }).map((_, row) => (
                            <div key={row} className="grid grid-cols-3 gap-3">
                                <SkeletonBlock className={cn("h-4", row % 2 === 0 ? "w-4/5" : "w-3/5")} />
                                <SkeletonBlock className={cn("h-4", row % 2 === 0 ? "w-3/5" : "w-4/5")} />
                                <SkeletonBlock className={cn("h-4", row % 3 === 0 ? "w-2/5" : "w-3/5")} />
                            </div>
                        ))}
                    </div>
                </>
            ) : null}

            {variant === "form" ? (
                <div className="space-y-4">
                    {Array.from({ length: resolvedItems }).map((_, row) => (
                        <div key={row} className="space-y-2">
                            <SkeletonBlock className={cn("h-3", row % 2 === 0 ? "w-24" : "w-32")} />
                            <SkeletonBlock className="h-10 w-full" rounded="lg" />
                        </div>
                    ))}
                    {showAction ? <SkeletonBlock className="h-10 w-36" rounded="lg" /> : null}
                </div>
            ) : null}
        </div>
    );
}

export function LoadingState({
    title = "Loading",
    description,
    lines = 3,
}: {
    title?: string;
    description?: string;
    lines?: number;
}) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>{title}</CardTitle>
                {description ? <CardDescription>{description}</CardDescription> : null}
            </CardHeader>
            <CardContent>
                <LoadingSkeleton lines={lines} />
            </CardContent>
        </Card>
    );
}

export function ErrorState({
    title = "Something went wrong",
    message = "Please retry.",
    onRetry,
    retryLabel = "Retry",
    supportHref,
    supportLabel = "Contact support",
    onContactSupport,
    troubleshootingTitle = "Try these quick checks",
    troubleshooting,
    actionHref,
    actionLabel,
    className,
}: {
    title?: string;
    message?: string;
    onRetry?: () => void | Promise<void>;
    retryLabel?: string;
    supportHref?: string;
    supportLabel?: string;
    onContactSupport?: () => void;
    troubleshootingTitle?: string;
    troubleshooting?: string[];
    actionHref?: string;
    actionLabel?: string;
    className?: string;
}) {
    const resolvedTroubleshooting =
        troubleshooting && troubleshooting.length > 0
            ? troubleshooting
            : ["Check your internet connection.", "Confirm you have access to this resource.", "Try refreshing the page."];

    return (
        <Card className={className}>
            <CardHeader>
                <div className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-xl border border-destructive/20 bg-destructive/10 text-destructive shrink-0">
                        <CircleAlert className="h-5 w-5" aria-hidden />
                    </div>
                    <div className="min-w-0">
                        <CardTitle>{title}</CardTitle>
                        <CardDescription role="alert" aria-live="assertive">
                            {message}
                        </CardDescription>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="rounded-xl border border-border bg-muted/30 p-3">
                    <div className="text-xs font-semibold text-foreground">{troubleshootingTitle}</div>
                    <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                        {resolvedTroubleshooting.map((item, i) => (
                            <li key={i} className="flex items-start gap-2">
                                <span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-muted-foreground/60 shrink-0" aria-hidden />
                                <span className="min-w-0">{item}</span>
                            </li>
                        ))}
                    </ul>
                </div>

                <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
                    {supportHref ? (
                        <Button asChild variant="secondary">
                            <Link href={supportHref}>
                                <MessageCircleWarning className="h-4 w-4" aria-hidden />
                                {supportLabel}
                            </Link>
                        </Button>
                    ) : onContactSupport ? (
                        <Button type="button" variant="secondary" onClick={onContactSupport}>
                            <MessageCircleWarning className="h-4 w-4" aria-hidden />
                            {supportLabel}
                        </Button>
                    ) : null}

                    {actionHref ? (
                        <Button asChild variant="outline">
                            <Link href={actionHref}>
                                <Plus className="h-4 w-4" aria-hidden />
                                {actionLabel ?? "Open"}
                            </Link>
                        </Button>
                    ) : null}

                    {onRetry ? (
                        <Button type="button" onClick={() => void onRetry()}>
                            <RotateCcw className="h-4 w-4" aria-hidden />
                            {retryLabel}
                        </Button>
                    ) : null}
                </div>
            </CardContent>
        </Card>
    );
}

export function EmptyState({
    title,
    message,
    kind = "generic",
    hint,
    primaryActionLabel,
    onPrimaryAction,
    primaryActionHref,
    primaryActionAriaLabel,
    secondaryActionLabel,
    onSecondaryAction,
    secondaryActionHref,
    actionLabel,
    onAction,
    actionHref,
    actionAriaLabel,
    className,
}: {
    title: string;
    message: string;
    kind?: "generic" | "search" | "inbox" | "email" | "uploads" | "connectors";
    hint?: string;
    primaryActionLabel?: string;
    onPrimaryAction?: () => void;
    primaryActionHref?: string;
    primaryActionAriaLabel?: string;
    secondaryActionLabel?: string;
    onSecondaryAction?: () => void;
    secondaryActionHref?: string;
    actionLabel?: string;
    onAction?: () => void;
    actionHref?: string;
    actionAriaLabel?: string;
    className?: string;
}) {
    const Icon: ComponentType<{ className?: string }> =
        kind === "search"
            ? Search
            : kind === "inbox"
              ? Inbox
              : kind === "email"
                ? Mail
                : kind === "uploads"
                  ? Upload
                  : kind === "connectors"
                    ? RotateCcw
                    : Inbox;

    const resolvedPrimaryLabel = primaryActionLabel ?? actionLabel;
    const resolvedPrimaryHref = primaryActionHref ?? actionHref;
    const resolvedOnPrimary = onPrimaryAction ?? onAction;
    const resolvedPrimaryAriaLabel = primaryActionAriaLabel ?? actionAriaLabel;

    return (
        <Card className={className}>
            <CardHeader>
                <div className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-muted text-muted-foreground shrink-0">
                        <Icon className="h-5 w-5" aria-hidden />
                    </div>
                    <div className="min-w-0">
                        <CardTitle>{title}</CardTitle>
                        <CardDescription>{message}</CardDescription>
                        {hint ? <div className="mt-2 text-sm text-muted-foreground">{hint}</div> : null}
                    </div>
                </div>
            </CardHeader>
            {resolvedPrimaryLabel && resolvedPrimaryHref ? (
                <CardContent className="flex flex-col gap-2 sm:flex-row sm:justify-end">
                    {secondaryActionLabel && secondaryActionHref ? (
                        <Button asChild variant="outline">
                            <Link href={secondaryActionHref}>
                                <Upload className="h-4 w-4" aria-hidden />
                                {secondaryActionLabel}
                            </Link>
                        </Button>
                    ) : secondaryActionLabel && onSecondaryAction ? (
                        <Button type="button" variant="outline" onClick={onSecondaryAction}>
                            <Upload className="h-4 w-4" aria-hidden />
                            {secondaryActionLabel}
                        </Button>
                    ) : null}

                    <Button asChild>
                        <Link href={resolvedPrimaryHref} aria-label={resolvedPrimaryAriaLabel}>
                            <Plus className="h-4 w-4" aria-hidden />
                            {resolvedPrimaryLabel}
                        </Link>
                    </Button>
                </CardContent>
            ) : resolvedPrimaryLabel && resolvedOnPrimary ? (
                <CardContent className="flex flex-col gap-2 sm:flex-row sm:justify-end">
                    {secondaryActionLabel && secondaryActionHref ? (
                        <Button asChild variant="outline">
                            <Link href={secondaryActionHref}>
                                <Upload className="h-4 w-4" aria-hidden />
                                {secondaryActionLabel}
                            </Link>
                        </Button>
                    ) : secondaryActionLabel && onSecondaryAction ? (
                        <Button type="button" variant="outline" onClick={onSecondaryAction}>
                            <Upload className="h-4 w-4" aria-hidden />
                            {secondaryActionLabel}
                        </Button>
                    ) : null}

                    <Button type="button" onClick={resolvedOnPrimary} aria-label={resolvedPrimaryAriaLabel}>
                        <Plus className="h-4 w-4" aria-hidden />
                        {resolvedPrimaryLabel}
                    </Button>
                </CardContent>
            ) : secondaryActionLabel && secondaryActionHref ? (
                <CardContent className="flex justify-end">
                    <Button asChild variant="outline">
                        <Link href={secondaryActionHref}>
                            <Upload className="h-4 w-4" aria-hidden />
                            {secondaryActionLabel}
                        </Link>
                    </Button>
                </CardContent>
            ) : secondaryActionLabel && onSecondaryAction ? (
                <CardContent className="flex justify-end">
                    <Button type="button" variant="outline" onClick={onSecondaryAction}>
                        <Upload className="h-4 w-4" aria-hidden />
                        {secondaryActionLabel}
                    </Button>
                </CardContent>
            ) : null}
        </Card>
    );
}
