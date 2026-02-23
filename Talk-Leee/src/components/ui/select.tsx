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
        const items = React.Children.toArray(children)
            .map((child) => (React.isValidElement(child) && child.type === "option" ? child : null))
            .filter(Boolean) as Array<React.ReactElement<{ value?: string; disabled?: boolean; children?: React.ReactNode }>>;

        return items.map((opt) => {
            const rawLabel = opt.props.children;
            const label = typeof rawLabel === "string" ? rawLabel : String(rawLabel ?? "");
            return {
                value: String(opt.props.value ?? ""),
                label,
                disabled: Boolean(opt.props.disabled),
            };
        });
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
    const [panelStyle, setPanelStyle] = useState<{ left: number; top: number; width: number } | null>(null);

    useEffect(() => {
        setMounted(true);
    }, []);

    useEffect(() => {
        if (!open) {
            setPanelStyle(null);
            return;
        }

        const updatePanelStyle = () => {
            const btn = buttonRef.current;
            if (!btn) return;
            const rect = btn.getBoundingClientRect();
            setPanelStyle({ left: rect.left, top: rect.bottom + 4, width: rect.width });
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
        if (!open) setActiveIndex(selectedIndex);
    }, [open, selectedIndex]);

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
                        "fixed z-[1000] overflow-hidden rounded-md border border-border bg-background shadow-md dark:border-zinc-800 dark:bg-zinc-900",
                        enhanceLight ? "ring-1 ring-emerald-500/20 drop-shadow-[0_10px_18px_rgba(16,185,129,0.22)]" : undefined
                    )}
                    style={{ left: panelStyle.left, top: panelStyle.top, width: panelStyle.width }}
                >
                    {options.map((opt, idx) => {
                        const isSelected = opt.value === value;
                        const isActive = idx === activeIndex;
                        return (
                            <button
                                key={`${opt.value}-${idx}`}
                                type="button"
                                role="option"
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
