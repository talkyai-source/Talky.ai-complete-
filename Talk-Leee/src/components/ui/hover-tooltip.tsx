"use client";

import { createPortal } from "react-dom";
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
}

export type HoverTooltipState =
    | { open: false }
    | { open: true; x: number; y: number; content: React.ReactNode; pinned?: boolean };

export function useHoverTooltip() {
    const [state, setState] = useState<HoverTooltipState>({ open: false });
    const stateRef = useRef<HoverTooltipState>(state);
    const hideTimerRef = useRef<number | null>(null);

    useLayoutEffect(() => {
        stateRef.current = state;
    }, [state]);

    const api = useMemo(() => {
        return {
            show: (
                x: number,
                y: number,
                content: React.ReactNode,
                options?: { pinned?: boolean; autoHideMs?: number }
            ) => {
                if (hideTimerRef.current) {
                    window.clearTimeout(hideTimerRef.current);
                    hideTimerRef.current = null;
                }

                const current = stateRef.current;
                const pinned = Boolean(options?.pinned);

                const shouldUpdate =
                    !current.open ||
                    current.pinned !== pinned ||
                    Math.abs(current.x - x) + Math.abs(current.y - y) > 2 ||
                    current.content !== content;

                if (shouldUpdate) setState({ open: true, x, y, content, pinned });

                if (options?.autoHideMs && options.autoHideMs > 0) {
                    hideTimerRef.current = window.setTimeout(() => {
                        setState({ open: false });
                        hideTimerRef.current = null;
                    }, options.autoHideMs);
                }
            },
            hide: () => {
                if (hideTimerRef.current) {
                    window.clearTimeout(hideTimerRef.current);
                    hideTimerRef.current = null;
                }
                setState({ open: false });
            },
        };
    }, []);

    return { state, ...api };
}

export function HoverTooltip({
    state,
    className,
    margin = 10,
}: {
    state: HoverTooltipState;
    className?: string;
    margin?: number;
}) {
    const ref = useRef<HTMLDivElement | null>(null);
    const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });

    useLayoutEffect(() => {
        if (!state.open) return;
        const el = ref.current;
        if (!el) return;

        const m = Math.max(10, margin);
        const rect = el.getBoundingClientRect();

        const preferredLeft = state.x;
        const preferredTop = state.y - 12;

        const left = clamp(preferredLeft - rect.width / 2, m, window.innerWidth - m - rect.width);
        const top = clamp(preferredTop - rect.height, m, window.innerHeight - m - rect.height);

        const id = window.requestAnimationFrame(() => {
            setPos({ left, top });
        });

        return () => window.cancelAnimationFrame(id);
    }, [margin, state]);

    if (typeof document === "undefined" || !state.open) return null;

    return createPortal(
        <div
            ref={ref}
            className={cn(
                "fixed z-[60] pointer-events-none select-none rounded-xl border border-gray-200/70 bg-white/95 backdrop-blur px-3 py-2 text-xs font-semibold text-gray-900 shadow-lg transition-opacity duration-300 ease-in-out",
                className
            )}
            style={{ left: pos.left, top: pos.top }}
            role="tooltip"
        >
            {state.content}
        </div>,
        document.body
    );
}
