"use client";

/**
 * InfoTip — the one explain-this control (goals.md §8).
 *
 * §8 asks for a single reusable component that works with a mouse, a keyboard
 * AND a touch screen, carries a short label plus optional "Learn more", and
 * never falls off the edge of the screen.
 *
 * WHY IT IS NOT JUST <Tooltip>
 * -----------------------------
 * A plain Radix tooltip opens on hover and focus and closes on blur. On a phone
 * there is no hover and no blur, so a hover-only tooltip is either permanently
 * shut or opens and will not go away. §8 lists mobile tap as a first-class
 * requirement, so this drives Radix in CONTROLLED mode and adds a "pinned"
 * state: hover/focus opens it transiently, a tap pins it open, tapping again or
 * pressing Escape closes it. One component, three input methods, no duplicated
 * content.
 *
 * WHAT MUST NOT GO IN HERE
 * -------------------------
 * §8: "Avoid hiding essential warnings only inside tooltips." Anything a user
 * must act on — a failure, a quota breach, a destructive consequence — belongs
 * in the page where it cannot be missed. This is for the sentence that turns a
 * label someone already read into something they understand. If the app is
 * unusable without reading the tip, the tip is in the wrong place.
 *
 * STAYING ON SCREEN
 * -----------------
 * `collisionPadding` plus Radix's flip/shift keeps the panel inside the
 * viewport on every side, which matters most on the narrow screens where the
 * tap path is the only path.
 */
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { Info } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { cn } from "@/lib/utils";

export type InfoTipProps = {
    /** The explanation. Keep it to a sentence or two. */
    children: React.ReactNode;
    /**
     * Accessible name for the trigger, e.g. "About tokens". Screen readers
     * announce this, so "more info" on nine controls is nine identical buttons.
     */
    label: string;
    /** Optional deeper reading, rendered as a "Learn more" link. */
    learnMoreHref?: string;
    learnMoreLabel?: string;
    side?: "top" | "right" | "bottom" | "left";
    className?: string;
};

export function InfoTip({
    children,
    label,
    learnMoreHref,
    learnMoreLabel = "Learn more",
    side = "top",
    className,
}: InfoTipProps) {
    const [open, setOpen] = React.useState(false);
    // Pinned = opened by tap/click. Survives the pointer leaving, which is the
    // whole point on a touch screen.
    const [pinned, setPinned] = React.useState(false);

    const close = React.useCallback(() => {
        setPinned(false);
        setOpen(false);
    }, []);

    // Escape closes a pinned tip. Radix handles this for its own dismissables,
    // but `pinned` is our state and it would otherwise survive.
    React.useEffect(() => {
        if (!pinned) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") close();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [pinned, close]);

    // A tap outside dismisses a pinned tip, the way any popover should.
    React.useEffect(() => {
        if (!pinned) return;
        const onDown = (e: PointerEvent) => {
            const target = e.target as HTMLElement | null;
            if (target?.closest("[data-info-tip]")) return;
            close();
        };
        window.addEventListener("pointerdown", onDown);
        return () => window.removeEventListener("pointerdown", onDown);
    }, [pinned, close]);

    return (
        <TooltipPrimitive.Provider delayDuration={150}>
            <TooltipPrimitive.Root
                open={open || pinned}
                onOpenChange={(next) => {
                    // While pinned, ignore Radix's hover-out close — only an
                    // explicit tap, Escape or an outside click should dismiss.
                    if (pinned) return;
                    setOpen(next);
                }}
            >
                <TooltipPrimitive.Trigger asChild>
                    <button
                        type="button"
                        data-info-tip=""
                        aria-label={label}
                        // The trigger is a real button, so keyboard focus and
                        // Enter/Space work without any extra handling.
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setPinned((p) => !p);
                        }}
                        className={cn(
                            "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                            className,
                        )}
                    >
                        <Info className="h-3.5 w-3.5" aria-hidden />
                    </button>
                </TooltipPrimitive.Trigger>

                <TooltipPrimitive.Portal>
                    <TooltipPrimitive.Content
                        data-info-tip=""
                        side={side}
                        sideOffset={6}
                        // Keep it on screen on every edge — the reason this is a
                        // shared component rather than nine hand-placed divs.
                        collisionPadding={12}
                        avoidCollisions
                        className="z-50 max-w-[min(20rem,calc(100vw-1.5rem))] rounded-lg border border-border bg-popover px-3 py-2 text-xs leading-relaxed text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95"
                    >
                        <div className="space-y-1.5">
                            <div>{children}</div>
                            {learnMoreHref && (
                                <Link
                                    href={learnMoreHref}
                                    className="inline-block font-medium underline underline-offset-2"
                                >
                                    {learnMoreLabel}
                                </Link>
                            )}
                        </div>
                        <TooltipPrimitive.Arrow className="fill-popover" />
                    </TooltipPrimitive.Content>
                </TooltipPrimitive.Portal>
            </TooltipPrimitive.Root>
        </TooltipPrimitive.Provider>
    );
}

/**
 * A label with its explanation attached — the shape most call sites want, so
 * they do not each re-invent the flex row and the gap.
 */
export function LabelWithInfo({
    children,
    tip,
    label,
    learnMoreHref,
    className,
}: {
    children: React.ReactNode;
    tip: React.ReactNode;
    /** Accessible name for the trigger; defaults to "About <children>". */
    label?: string;
    learnMoreHref?: string;
    className?: string;
}) {
    const accessibleName =
        label ?? `About ${typeof children === "string" ? children : "this setting"}`;
    return (
        <span className={cn("inline-flex items-center gap-1.5", className)}>
            {children}
            <InfoTip label={accessibleName} learnMoreHref={learnMoreHref}>
                {tip}
            </InfoTip>
        </span>
    );
}
