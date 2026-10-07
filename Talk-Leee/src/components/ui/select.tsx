"use client";

import { cn } from "@/lib/utils";
import { useTheme } from "@/components/providers/theme-provider";
import { ChevronDown } from "lucide-react";
import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export function Select({
    value,
    onChange,
    children,
    className,
    selectClassName,
    ariaLabel,
    id,
    ariaInvalid,
    ariaDescribedBy,
    fitLongestOption,
    disabled,
    lightThemeGreen,
}: {
    value: string;
    onChange: (next: string) => void;
    children: React.ReactNode;
    className?: string;
    selectClassName?: string;
    ariaLabel: string;
    /** Forwarded to the trigger button so a <label htmlFor> keeps working. */
    id?: string;
    /** Forwarded as aria-invalid so error styling/announcement survives. */
    ariaInvalid?: boolean;
    /** Forwarded as aria-describedby so warning/hint text stays announced. */
    ariaDescribedBy?: string;
    /**
     * Auto-width parity with native <select>: size the closed trigger to the
     * LONGEST option (native behaviour) instead of the selected one, so the
     * control neither jumps on selection nor shrinks below its old footprint.
     * For width-constrained (w-full) layouts leave this off.
     */
    fitLongestOption?: boolean;
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
    const listboxId = useId();
    const [fitMinWidth, setFitMinWidth] = useState<number | undefined>(undefined);
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
        // fitLongestOption: measure the widest label with the trigger's own
        // font and reserve that width, as a native closed <select> does.
        if (!fitLongestOption) return;
        const btn = buttonRef.current;
        if (!btn) return;
        try {
            const ctx = document.createElement("canvas").getContext("2d");
            if (!ctx) return;
            const cs = window.getComputedStyle(btn);
            ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
            let max = 0;
            for (const opt of options) max = Math.max(max, ctx.measureText(opt.label).width);
            const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) + parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth);
            const next = Math.min(Math.ceil(max + pad) + 2, window.innerWidth - 16);
            setFitMinWidth(next);
        } catch { /* keep natural width */ }
    }, [fitLongestOption, options]);

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

            // Like a native popup, the panel may grow past a narrow trigger to
            // fit its longest option — but never past the screen. Canvas text
            // metrics; on failure (no 2D context) it falls back to the trigger
            // width, which was the previous behaviour.
            let contentWidth = 0;
            try {
                const ctx = document.createElement("canvas").getContext("2d");
                if (ctx) {
                    const cs = window.getComputedStyle(btn);
                    ctx.font = `400 14px ${cs.fontFamily}`; // option rows are text-sm
                    for (const opt of options) contentWidth = Math.max(contentWidth, ctx.measureText(opt.label).width);
                    contentWidth = Math.ceil(contentWidth) + 24 /* row px-3 */ + 18 /* scrollbar room */;
                }
            } catch { /* fall back to trigger width */ }
            // Hard viewport cap LAST: even a trigger mid-layout-animation (or
            // genuinely wider than a tiny screen) must never push the panel out.
            const width = Math.min(Math.max(rect.width, contentWidth), vw - 2 * MARGIN);

            const left = Math.max(MARGIN, Math.min(rect.left, vw - width - MARGIN));
            const spaceBelow = vh - rect.bottom - GAP - MARGIN;
            const spaceAbove = rect.top - GAP - MARGIN;
            const flip = spaceBelow < ROW * 3 && spaceAbove > spaceBelow;
            const maxHeight = Math.max(ROW, Math.min(320, flip ? spaceAbove : spaceBelow));

            setPanelStyle(
                flip
                    ? { left, width, maxHeight, bottom: vh - rect.top + GAP }
                    : { left, width, maxHeight, top: rect.bottom + GAP }
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
    }, [open, options]);

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

    // Native selects never land arrows on a disabled option; mirror that by
    // walking past them (staying put when nothing enabled lies beyond).
    const nextEnabled = (from: number, dir: 1 | -1) => {
        for (let i = from + dir; i >= 0 && i < options.length; i += dir) {
            if (!options[i].disabled) return i;
        }
        return null;
    };

    // Type-ahead buffer (native selects jump to options matching typed text);
    // entries older than a second start a fresh prefix.
    const typeaheadRef = useRef({ buffer: "", at: 0 });

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
            // The panel consumed this Escape; without this an enclosing Modal's
            // window-level keydown listener would close the modal as well.
            e.stopPropagation();
            setOpen(false);
            return;
        }

        if (e.key === "ArrowDown") {
            e.preventDefault();
            setActiveIndex((i) => nextEnabled(i, 1) ?? i);
            return;
        }

        if (e.key === "ArrowUp") {
            e.preventDefault();
            setActiveIndex((i) => nextEnabled(i, -1) ?? i);
            return;
        }

        if (e.key === "Enter") {
            e.preventDefault();
            commitValue(activeIndex);
            return;
        }

        // Type-ahead: single printable characters, no modifiers. A lone space
        // is left alone (it would hijack scrolling and native space-toggling).
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
            const ta = typeaheadRef.current;
            if (e.key === " " && ta.buffer === "") return;
            e.preventDefault();
            const now = e.timeStamp; // monotonic per-event time; pure to read
            if (now - ta.at > 1000) ta.buffer = "";
            ta.buffer += e.key.toLowerCase();
            ta.at = now;
            // A fresh single character searches from the NEXT option so
            // repeated presses cycle through matches, as native selects do.
            const start = ta.buffer.length === 1 ? activeIndex + 1 : activeIndex;
            for (let step = 0; step < options.length; step++) {
                const idx = (start + step) % options.length;
                const opt = options[idx];
                if (!opt.disabled && opt.label.trim().toLowerCase().startsWith(ta.buffer)) {
                    setActiveIndex(idx);
                    break;
                }
            }
        }
    };

    const panel =
        open && mounted && panelStyle
            ? createPortal(
                <div
                    ref={panelRef}
                    id={listboxId}
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
                id={id}
                // W3C "select-only combobox" pattern: combobox (not button) is
                // the role a native <select> exposes, and it supports aria-invalid.
                role="combobox"
                aria-label={ariaLabel}
                aria-haspopup="listbox"
                aria-controls={listboxId}
                aria-expanded={open}
                aria-invalid={ariaInvalid === undefined ? undefined : ariaInvalid}
                aria-describedby={ariaDescribedBy}
                disabled={disabled}
                style={fitMinWidth !== undefined ? { minWidth: fitMinWidth } : undefined}
                onClick={() => setOpen((v) => !v)}
                className={cn(
                    "flex h-10 w-full items-center rounded-md border border-input bg-background px-3 pr-9 text-left text-sm text-foreground shadow-sm transition-[background-color,border-color,box-shadow] duration-150 ease-out hover:bg-accent/20 hover:border-foreground/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20 disabled:cursor-not-allowed disabled:opacity-50",
                    // A long SELECTED label must not widen narrow grid/flex
                    // tracks: flex intrinsic sizing ignores min-width:0, so the
                    // nowrap label propagates unless intrinsic size is contained.
                    // fitLongestOption triggers skip this — they shrink-to-fit by
                    // design and their min-width already covers every option.
                    !fitLongestOption && "[contain:inline-size]",
                    selectClassName
                )}
            >
                {/* flex-1 basis-0 keeps the button's INTRINSIC width at its
                    padding: a long selected label must never widen a narrow
                    grid/flex track the way a nowrap span's min-content would. */}
                <span className="min-w-0 flex-1 truncate">{selectedLabel}</span>
            </button>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            {panel}
        </div>
    );
}
