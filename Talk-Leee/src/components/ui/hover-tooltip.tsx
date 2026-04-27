"use client";

import { createPortal } from "react-dom";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
}

export type HoverTooltipState =
    | { open: false }
    | { open: true; content: React.ReactNode; pinned?: boolean };

export function useHoverTooltip() {
    const [state, setState] = useState<HoverTooltipState>({ open: false });
    const stateRef = useRef<HoverTooltipState>(state);
    const posRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
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
                posRef.current = { x, y };
                if (hideTimerRef.current) {
                    window.clearTimeout(hideTimerRef.current);
                    hideTimerRef.current = null;
                }

                const current = stateRef.current;
                const pinned = Boolean(options?.pinned);

                const shouldUpdate =
                    !current.open ||
                    current.pinned !== pinned ||
                    current.content !== content;

                if (shouldUpdate) setState({ open: true, content, pinned });

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

    return { state, posRef, ...api };
}

export function HoverTooltip({
    tooltip,
    className,
    margin = 10,
}: {
    tooltip: { state: HoverTooltipState; posRef: React.MutableRefObject<{ x: number; y: number }> };
    className?: string;
    margin?: number;
}) {
    const ref = useRef<HTMLDivElement | null>(null);
    const sizeRef = useRef<{ width: number; height: number }>({ width: 0, height: 0 });

    useLayoutEffect(() => {
        if (!tooltip.state.open) return;
        const el = ref.current;
        if (!el) return;

        const updateSize = () => {
            const rect = el.getBoundingClientRect();
            sizeRef.current = { width: rect.width, height: rect.height };
        };

        updateSize();
        const ro = new ResizeObserver(() => updateSize());
        ro.observe(el);
        return () => ro.disconnect();
    }, [tooltip.state]);

    useEffect(() => {
        if (!tooltip.state.open) return;
        const el = ref.current;
        if (!el) return;

        const m = Math.max(10, margin);
        const isPinned = Boolean(tooltip.state.pinned);

        const applyPosition = () => {
            const { x, y } = tooltip.posRef.current;
            const { width, height } = sizeRef.current;
            const preferredLeft = x;
            const preferredTop = y - 12;
            const left = clamp(preferredLeft - width / 2, m, window.innerWidth - m - width);
            const top = clamp(preferredTop - height, m, window.innerHeight - m - height);
            el.style.transform = `translate3d(${left}px, ${top}px, 0)`;
        };

        applyPosition();
        if (isPinned) return;

        let rafId = 0;
        const tick = () => {
            applyPosition();
            rafId = window.requestAnimationFrame(tick);
        };

        rafId = window.requestAnimationFrame(tick);
        return () => window.cancelAnimationFrame(rafId);
    }, [margin, tooltip]);

    if (typeof document === "undefined" || !tooltip.state.open) return null;

    return createPortal(
        <div
            ref={ref}
            className={cn(
                "fixed z-[60] pointer-events-none select-none rounded-xl border border-gray-200/70 bg-white/95 backdrop-blur px-3 py-2 text-xs font-semibold text-gray-900 shadow-lg transition-opacity duration-300 ease-in-out",
                className
            )}
            style={{ left: 0, top: 0, transform: "translate3d(0px, 0px, 0)" }}
            role="tooltip"
        >
            {tooltip.state.content}
        </div>,
        document.body
    );
}
