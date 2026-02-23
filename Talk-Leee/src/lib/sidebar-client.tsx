"use client";

import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { SidebarState } from "@/lib/sidebar";
import { sidebarStore } from "@/lib/sidebar";

function useSidebarSnapshot(): SidebarState {
    const snapshot = useSyncExternalStore(
        (listener) => sidebarStore.subscribe(listener),
        () => sidebarStore.getSnapshot(),
        () => sidebarStore.getSnapshot()
    );

    useEffect(() => {
        sidebarStore.hydrateIfNeeded();
    }, []);

    return snapshot;
}

export function useSidebarState() {
    return useSidebarSnapshot();
}

export function useSidebarActions() {
    const setCollapsed = useCallback((next: boolean) => sidebarStore.setCollapsed(next), []);
    const setMobileOpen = useCallback((next: boolean) => sidebarStore.setMobileOpen(next), []);
    const toggleCollapsed = useCallback(() => sidebarStore.toggleCollapsed(), []);
    const toggleMobile = useCallback(() => sidebarStore.toggleMobile(), []);
    const closeMobile = useCallback(() => sidebarStore.closeMobile(), []);

    const toggle = useCallback(() => {
        if (typeof window === "undefined") return;
        const isDesktop = window.matchMedia?.("(min-width: 1024px)")?.matches;
        if (isDesktop) toggleCollapsed();
        else toggleMobile();
    }, [toggleCollapsed, toggleMobile]);

    return useMemo(
        () => ({
            setCollapsed,
            setMobileOpen,
            toggleCollapsed,
            toggleMobile,
            closeMobile,
            toggle,
        }),
        [closeMobile, setCollapsed, setMobileOpen, toggle, toggleCollapsed, toggleMobile]
    );
}

