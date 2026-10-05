"use client";

import { cn } from "@/lib/utils";
import { useTheme } from "@/components/providers/theme-provider";
import { ChevronDown } from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export function Select({
    value,
    onChange,
    children,
    className,
    selectClassName,
    ariaLabel,
    disabled,
    lightThemeGreen,
}: {
    value: string;
    onChange: (next: string) => void;
    children: React.ReactNode;
    className?: string;
    selectClassName?: string;
    ariaLabel: string;
    disabled?: boolean;
    lightThemeGreen?: boolean;
}) {
    const { theme } = useTheme();
    const enhanceLight = Boolean(lightThemeGreen && theme === "light");

    const options = useMemo(() => {
        // Reads direct <option> children AND options nested in <optgroup>, in
        // markup order, so grouped options are never silently dropped.
        type OptionEl = React.ReactElement<{ value?: string; disabled?: boolean; children?: React.ReactNode }>;
        const out: Array<{ value: string; label: string; disabled: boolean; group?: string }> = [];
        const pushOption = (opt: OptionEl, group?: string) => {
            const rawLabel = opt.props.children;
            out.push({
                value: String(opt.props.value ?? ""),
                label: typeof rawLabel === "string" ? rawLabel : String(rawLabel ?? ""),
                disabled: Boolean(opt.props.disabled),
                group,
            });
        };
        for (const child of React.Children.toArray(children)) {
            if (!React.isValidElement(child)) continue;
            if (child.type === "option") {
                pushOption(child as OptionEl);
            } else if (child.type === "optgroup") {
                const grp = child as React.ReactElement<{ label?: string; children?: React.ReactNode }>;
                const groupLabel = typeof grp.props.label === "string" ? grp.props.label : "";
                for (const sub of React.Children.toArray(grp.props.children)) {
                    if (React.isValidElement(sub) && sub.type === "option") pushOption(sub as OptionEl, groupLabel);
                }
            }
        }
        return out;
    }, [children]);

    const selectedIndex = Math.max(
        0,
        options.findIndex((o) => o.value === value)
    );
    const selectedLabel = options.find((o) => o.value === value)?.label ?? "";

    const [open, setOpen] = useState(false);
    const [activeIndex, setActiveIndex] = useState(selectedIndex);
    const rootRef = useRef<HTMLDivElement>(null);
    const buttonRef = useRef<HTMLButtonElement>(null);
    const panelRef = useRef<HTMLDivElement>(null);
    const [mounted, setMounted] = useState(false);
    // top is set when the panel opens below the trigger, bottom when it flips
    // above it; maxHeight is whatever actually fits on the chosen side.
    const [panelStyle, setPanelStyle] = useState<{ left: number; width: number; maxHeight: number; top?: number; bottom?: number } | null>(null);

    useEffect(() => {
        // Portal (createPortal to document.body) hydration gate — document
        // isn't safely available for portal rendering until after mount.
        // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration-mounted flag gating the document.body portal
        setMounted(true);
    }, []);

    useEffect(() => {
        if (!open) {
            // Clear stale panel position once closed, and recomputed fresh
            // (via getBoundingClientRect below) the next time it opens.
            // eslint-disable-next-line react-hooks/set-state-in-effect -- clears stale panel position on close; recomputed from DOM on next open
            setPanelStyle(null);
            return;
        }

        const updatePanelStyle = () => {
            const btn = buttonRef.current;
            if (!btn) return;
            const rect = btn.getBoundingClientRect();
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const MARGIN = 8;   // panel never sits closer than this to a screen edge
            const GAP = 4;      // gap between trigger and panel
            const ROW = 36;     // measured option-row height; flip once <3 rows fit below

            const left = Math.max(MARGIN, Math.min(rect.left, vw - rect.width - MARGIN));
            const spaceBelow = vh - rect.bottom - GAP - MARGIN;
            const spaceAbove = rect.top - GAP - MARGIN;
            const flip = spaceBelow < ROW * 3 && spaceAbove > spaceBelow;
            const maxHeight = Math.max(ROW, Math.min(320, flip ? spaceAbove : spaceBelow));

            setPanelStyle(
                flip
                    ? { left, width: rect.width, maxHeight, bottom: vh - rect.top + GAP }
                    : { left, width: rect.width, maxHeight, top: rect.bottom + GAP }
            );
        };

        updatePanelStyle();

        const onResize = () => updatePanelStyle();
        const onScroll = () => updatePanelStyle();

        window.addEventListener("resize", onResize);
        window.addEventListener("scroll", onScroll, { capture: true });
        return () => {
            window.removeEventListener("resize", onResize);
            window.removeEventListener("scroll", onScroll, { capture: true } as AddEventListenerOptions);
        };
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const onPointerDown = (e: PointerEvent) => {
            const root = rootRef.current;
            const panel = panelRef.current;
            if (!root) return;
            if (e.target instanceof Node && (root.contains(e.target) || panel?.contains(e.target))) return;
            setOpen(false);
        };
        window.addEventListener("pointerdown", onPointerDown, { capture: true });
        return () => window.removeEventListener("pointerdown", onPointerDown, { capture: true } as AddEventListenerOptions);
    }, [open]);

    useEffect(() => {
        // Reset keyboard-active option to the selected one whenever the
        // popover closes, so reopening starts highlighting the current value.
        // eslint-disable-next-line react-hooks/set-state-in-effect -- resets active option to selection on close, not derivable during render
        if (!open) setActiveIndex(selectedIndex);
    }, [open, selectedIndex]);

    useEffect(() => {
        // The panel scrolls now, so keep the keyboard-highlighted option in
        // view while arrowing; otherwise Enter would select something unseen.
        if (!open) return;
        const el = panelRef.current?.querySelector(`[data-option-index="${activeIndex}"]`);
        if (el instanceof HTMLElement) el.scrollIntoView({ block: "nearest" });
    }, [open, activeIndex]);

    const commitValue = (idx: number) => {
        const opt = options[idx];
        if (!opt || opt.disabled) return;
        onChange(opt.value);
        setOpen(false);
        buttonRef.current?.focus();
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        if (disabled) return;

        if (!open) {
            if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setOpen(true);
            }
            return;
        }

        if (e.key === "Escape") {
            e.preventDefault();
            setOpen(false);
            return;
        }

        if (e.key === "ArrowDown") {
            e.preventDefault();
            setActiveIndex((i) => Math.min(options.length - 1, i + 1));
            return;
        }

        if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => Math.max(0, i - 1));
            return;
        }

        if (e.key === "Enter") {
            e.preventDefault();
            commitValue(activeIndex);
        }
    };

    const panel =
        open && mounted && panelStyle
            ? createPortal(
                <div
                    ref={panelRef}
                    role="listbox"
                    aria-label={ariaLabel}
                    className={cn(
                        "fixed z-[1000] overflow-y-auto rounded-md border border-border bg-background shadow-md dark:border-zinc-800 dark:bg-zinc-900",
                        enhanceLight ? "ring-1 ring-emerald-500/20 drop-shadow-[0_10px_18px_rgba(16,185,129,0.22)]" : undefined
                    )}
                    style={{
                        left: panelStyle.left,
                        top: panelStyle.top,
                        bottom: panelStyle.bottom,
                        width: panelStyle.width,
                        maxHeight: panelStyle.maxHeight,
                    }}
                >
                    {options.map((opt, idx) => {
                        const isSelected = opt.value === value;
                        const isActive = idx === activeIndex;
                        // Non-interactive group heading before the first option
                        // of each <optgroup>; keyboard indexes skip headings.
                        const showGroup = Boolean(opt.group) && (idx === 0 || options[idx - 1].group !== opt.group);
                        return (
                            <React.Fragment key={`${opt.value}-${idx}`}>
                            {showGroup ? (
                                <div
                                    role="presentation"
                                    className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground"
                                >
                                    {opt.group}
                                </div>
                            ) : null}
                            <button
                                type="button"
                                role="option"
                                data-option-index={idx}
                                aria-selected={isSelected}
                                disabled={opt.disabled}
                                onMouseEnter={() => setActiveIndex(idx)}
                                onClick={() => commitValue(idx)}
                                className={cn(
                                    "flex w-full items-center px-3 py-2 text-left text-sm transition-colors",
                                    opt.disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
                                    isSelected
                                        ? "bg-muted text-foreground dark:bg-zinc-800 dark:text-white"
                                        : cn(
                                            "text-foreground dark:text-white/90 dark:hover:bg-zinc-800",
                                            enhanceLight ? "hover:bg-emerald-100 hover:text-gray-900" : "hover:bg-muted/60"
                                        ),
                                    isActive && !isSelected
                                        ? cn(
                                            "dark:bg-zinc-800",
                                            enhanceLight ? "bg-emerald-100 text-gray-900" : "bg-muted/60"
                                        )
                                        : ""
                                )}
                            >
                                <span className="min-w-0 truncate">{opt.label}</span>
                            </button>
                            </React.Fragment>
                        );
                    })}
                </div>,
                document.body
            )
            : null;

    return (
        <div ref={rootRef} className={cn("relative", className)} onKeyDown={onKeyDown}>
            <button
                ref={buttonRef}
                type="button"
                aria-label={ariaLabel}
                aria-haspopup="listbox"
                aria-expanded={open}
                disabled={disabled}
                onClick={() => setOpen((v) => !v)}
                className={cn(
                    "flex h-10 w-full items-center rounded-md border border-input bg-background px-3 pr-9 text-left text-sm text-foreground shadow-sm transition-[background-color,border-color,box-shadow] duration-150 ease-out hover:bg-accent/20 hover:border-foreground/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20 disabled:cursor-not-allowed disabled:opacity-50",
                    selectClassName
                )}
            >
                <span className="min-w-0 truncate">{selectedLabel}</span>
            </button>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            {panel}
        </div>
    );
}
