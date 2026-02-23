"use client";

import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { createPortal } from "react-dom";
import { useEffect, useId, useMemo, useRef } from "react";
import { cn } from "@/lib/utils";

function getFocusableElements(container: HTMLElement) {
    const selector =
        'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    const all = Array.from(container.querySelectorAll<HTMLElement>(selector));
    return all.filter((el) => {
        if (el.hasAttribute("disabled")) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === "hidden" || style.display === "none") return false;
        if (el.getAttribute("aria-hidden") === "true") return false;
        return true;
    });
}

export function Modal({
    open,
    onOpenChange,
    title,
    description,
    ariaLabel,
    size = "md",
    trapFocus = true,
    initialFocusRef,
    children,
    footer,
}: {
    open: boolean;
    onOpenChange: (next: boolean) => void;
    title?: string;
    description?: string;
    ariaLabel?: string;
    size?: "sm" | "md" | "lg" | "xl";
    trapFocus?: boolean;
    initialFocusRef?: React.RefObject<HTMLElement | null>;
    children: React.ReactNode;
    footer?: React.ReactNode;
}) {
    const titleId = useId();
    const descId = useId();
    const panelRef = useRef<HTMLDivElement | null>(null);
    const disableMotion = Boolean((globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: unknown }).IS_REACT_ACT_ENVIRONMENT);

    useEffect(() => {
        if (!open) return;
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key === "Escape") onOpenChange(false);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [open, onOpenChange]);

    useEffect(() => {
        if (!open) return;
        const prevOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
        return () => {
            document.body.style.overflow = prevOverflow;
        };
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const el = panelRef.current;
        const initial = initialFocusRef?.current ?? null;
        const toFocus = initial ?? el;
        if (!toFocus) return;
        const t = window.setTimeout(() => toFocus.focus(), 0);
        return () => window.clearTimeout(t);
    }, [initialFocusRef, open]);

    const sizeClass = useMemo(() => {
        if (size === "sm") return "max-w-md";
        if (size === "lg") return "max-w-3xl";
        if (size === "xl") return "max-w-5xl";
        return "max-w-xl";
    }, [size]);

    if (typeof document === "undefined") return null;

    return createPortal(
        disableMotion ? (
            open ? (
                <div className="fixed inset-0 z-50">
                    <div aria-hidden="true" className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={() => onOpenChange(false)} />

                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-label={ariaLabel}
                        aria-labelledby={title ? titleId : undefined}
                        aria-describedby={description ? descId : undefined}
                        tabIndex={-1}
                        ref={panelRef}
                        onKeyDown={(e) => {
                            if (!trapFocus) return;
                            if (e.key !== "Tab") return;
                            const panel = panelRef.current;
                            if (!panel) return;
                            const focusable = getFocusableElements(panel);
                            if (focusable.length === 0) {
                                e.preventDefault();
                                panel.focus();
                                return;
                            }

                            const first = focusable[0]!;
                            const last = focusable[focusable.length - 1]!;
                            const active = document.activeElement as HTMLElement | null;

                            if (e.shiftKey) {
                                if (!active || active === first || active === panel) {
                                    e.preventDefault();
                                    last.focus();
                                }
                                return;
                            }

                            if (!active || active === last) {
                                e.preventDefault();
                                first.focus();
                            }
                        }}
                        className={cn(
                            "absolute left-1/2 top-1/2 w-[calc(100%-24px)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border bg-background/90 text-foreground shadow-2xl outline-none backdrop-blur-xl",
                            sizeClass
                        )}
                    >
                        {(title || description) && (
                            <div className="border-b border-border px-5 py-4">
                                {title ? (
                                    <div id={titleId} className="text-base font-semibold text-foreground">
                                        {title}
                                    </div>
                                ) : null}
                                {description ? (
                                    <div id={descId} className="mt-1 text-sm text-muted-foreground">
                                        {description}
                                    </div>
                                ) : null}
                            </div>
                        )}
                        <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
                        {footer ? <div className="border-t border-border px-5 py-4">{footer}</div> : null}
                    </div>
                </div>
            ) : null
        ) : (
            <AnimatePresence>
                {open ? (
                    <div className="fixed inset-0 z-50">
                        <motion.div
                            aria-hidden="true"
                            className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.16 }}
                            onClick={() => onOpenChange(false)}
                        />

                        <motion.div
                            role="dialog"
                            aria-modal="true"
                            aria-label={ariaLabel}
                            aria-labelledby={title ? titleId : undefined}
                            aria-describedby={description ? descId : undefined}
                            tabIndex={-1}
                            ref={panelRef}
                            onKeyDown={(e) => {
                                if (!trapFocus) return;
                                if (e.key !== "Tab") return;
                                const panel = panelRef.current;
                                if (!panel) return;
                                const focusable = getFocusableElements(panel);
                                if (focusable.length === 0) {
                                    e.preventDefault();
                                    panel.focus();
                                    return;
                                }

                                const first = focusable[0]!;
                                const last = focusable[focusable.length - 1]!;
                                const active = document.activeElement as HTMLElement | null;

                                if (e.shiftKey) {
                                    if (!active || active === first || active === panel) {
                                        e.preventDefault();
                                        last.focus();
                                    }
                                    return;
                                }

                                if (!active || active === last) {
                                    e.preventDefault();
                                    first.focus();
                                }
                            }}
                            className={cn(
                                "absolute left-1/2 top-1/2 w-[calc(100%-24px)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border bg-background/90 text-foreground shadow-2xl outline-none backdrop-blur-xl",
                                sizeClass
                            )}
                            initial={{ y: 12, opacity: 0, scale: 0.985 }}
                            animate={{ y: 0, opacity: 1, scale: 1 }}
                            exit={{ y: 10, opacity: 0, scale: 0.985 }}
                            transition={{ type: "spring", stiffness: 260, damping: 24 }}
                        >
                            {(title || description) && (
                                <div className="border-b border-border px-5 py-4">
                                    {title ? (
                                        <div id={titleId} className="text-base font-semibold text-foreground">
                                            {title}
                                        </div>
                                    ) : null}
                                    {description ? (
                                        <div id={descId} className="mt-1 text-sm text-muted-foreground">
                                            {description}
                                        </div>
                                    ) : null}
                                </div>
                            )}
                            <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
                            {footer ? <div className="border-t border-border px-5 py-4">{footer}</div> : null}
                        </motion.div>
                    </div>
                ) : null}
            </AnimatePresence>
        ),
        document.body
    );
}
