"use client";

import { useEffect, useMemo, useState } from "react";
import { Menu, PanelLeftClose } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSidebarActions, useSidebarState } from "@/lib/sidebar-client";

export function GlobalSidebarToggle({ className }: { className?: string }) {
    const { collapsed, mobileOpen } = useSidebarState();
    const { toggle } = useSidebarActions();
    const [isDesktop, setIsDesktop] = useState(false);

    useEffect(() => {
        if (typeof window === "undefined") return;
        const mql = window.matchMedia("(min-width: 1024px)");
        const update = () => setIsDesktop(mql.matches);
        update();
        mql.addEventListener("change", update);
        return () => mql.removeEventListener("change", update);
    }, []);

    const expanded = isDesktop ? !collapsed : mobileOpen;
    const Icon = expanded ? PanelLeftClose : Menu;

    const leftStyle = useMemo(() => {
        if (!isDesktop) return 12;
        return `calc(${expanded ? "var(--sidebar-expanded-width)" : "var(--sidebar-collapsed-width)"} + 12px)`;
    }, [expanded, isDesktop]);

    return (
        <button
            type="button"
            onClick={toggle}
            aria-label="Toggle sidebar"
            aria-expanded={expanded}
            className={cn(
                "fixed top-3 z-[70] inline-flex h-12 w-12 items-center justify-center rounded-full bg-transparent text-foreground/80 transition-colors duration-200 ease-in-out hover:bg-foreground/5 hover:text-foreground active:bg-foreground/8 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20",
                className
            )}
            style={{ left: leftStyle, willChange: "transform" }}
        >
            <Icon className="h-5 w-5" aria-hidden />
        </button>
    );
}

