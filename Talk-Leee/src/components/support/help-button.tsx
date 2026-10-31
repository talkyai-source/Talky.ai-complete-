"use client";

import { LifeBuoy } from "lucide-react";
import { cn } from "@/lib/utils";

/*
 * The floating Help launcher. The text assistant's launcher owns the
 * bottom-right corner (fixed bottom-5 right-4, 48px, sm:bottom-6 sm:right-6),
 * so this sits directly above it: 20 + 48 + 12 = 80px on small screens and
 * 24 + 48 + 12 = 84px from sm up, same right edge, one layer below it.
 */
export function HelpButton({ onClick, open, className }: { onClick: () => void; open: boolean; className?: string }) {
    return (
        <button
            type="button"
            data-support-help-button
            onClick={onClick}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-label="Help"
            title="Help"
            className={cn(
                "fixed bottom-20 right-4 sm:bottom-21 sm:right-6 z-40 inline-flex h-12 w-12 items-center justify-center rounded-full",
                "bg-background text-foreground border border-border shadow-lg transition-[transform,box-shadow] hover:scale-105 hover:shadow-xl",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                className,
            )}
        >
            <LifeBuoy className="h-5 w-5" aria-hidden />
        </button>
    );
}
