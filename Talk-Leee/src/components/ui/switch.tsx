"use client";

import { cn } from "@/lib/utils";

export function Switch({
    checked,
    onCheckedChange,
    ariaLabel,
    disabled,
    className,
}: {
    checked: boolean;
    onCheckedChange: (next: boolean) => void;
    ariaLabel: string;
    disabled?: boolean;
    className?: string;
}) {
    return (
        <button
            type="button"
            role="switch"
            aria-checked={checked}
            aria-label={ariaLabel}
            disabled={disabled}
            onClick={() => onCheckedChange(!checked)}
            className={cn(
                "relative inline-flex h-6 w-11 items-center rounded-full border border-input bg-muted/60 transition-[background-color,border-color,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
                checked ? "bg-foreground border-foreground" : "hover:bg-muted",
                className
            )}
        >
            <span
                aria-hidden
                className={cn(
                    "inline-block h-5 w-5 translate-x-0.5 rounded-full bg-background shadow-sm transition-transform",
                    checked ? "translate-x-[1.375rem]" : ""
                )}
            />
        </button>
    );
}
