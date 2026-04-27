"use client";

import React, { createContext, useContext, useEffect, useMemo } from "react";
import type { WhiteLabelBranding } from "@/lib/white-label/branding";

type WhiteLabelBrandingContextValue = {
    branding: WhiteLabelBranding;
};

const WhiteLabelBrandingContext = createContext<WhiteLabelBrandingContextValue | null>(null);

export function useWhiteLabelBranding() {
    return useContext(WhiteLabelBrandingContext);
}

function buildWhiteLabelCssVars(branding: WhiteLabelBranding) {
    const primary = branding.colors.primary;
    const secondary = branding.colors.secondary;

    return {
        ["--primary" as string]: primary,
        ["--ring" as string]: primary,
        ["--secondary" as string]: secondary,
        ["--accent" as string]: secondary,
        ["--sidebar-primary" as string]: primary,
        ["--sidebar-ring" as string]: primary,
        ["--sidebar-accent" as string]: secondary,
        ["--primary-foreground" as string]: "#FFFFFF",
        ["--sidebar-primary-foreground" as string]: "#FFFFFF",
    } as React.CSSProperties;
}

function applyFavicon(branding: WhiteLabelBranding) {
    const href = `${branding.favicon.src}${branding.favicon.src.includes("?") ? "&" : "?"}wl=${encodeURIComponent(branding.partnerId)}&v=${encodeURIComponent(branding.version)}`;
    const type = branding.favicon.type ?? "image/svg+xml";

    const head = document.head;
    const existing = head.querySelectorAll('link[data-wl-favicon="1"]');
    existing.forEach((node) => node.remove());

    const link = document.createElement("link");
    link.setAttribute("rel", "icon");
    link.setAttribute("href", href);
    link.setAttribute("type", type);
    link.setAttribute("data-wl-favicon", "1");
    head.appendChild(link);
}

export function WhiteLabelBrandingProvider({ branding, children }: { branding: WhiteLabelBranding; children: React.ReactNode }) {
    const value = useMemo<WhiteLabelBrandingContextValue>(() => ({ branding }), [branding]);
    const style = useMemo(() => buildWhiteLabelCssVars(branding), [branding]);

    useEffect(() => {
        applyFavicon(branding);
        return () => {
            const existing = document.head.querySelectorAll('link[data-wl-favicon="1"]');
            existing.forEach((node) => node.remove());
        };
    }, [branding]);

    return (
        <WhiteLabelBrandingContext.Provider value={value}>
            <div data-white-label-partner={branding.partnerId} style={style}>
                {children}
            </div>
        </WhiteLabelBrandingContext.Provider>
    );
}
