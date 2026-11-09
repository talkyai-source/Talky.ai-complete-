"use client";

import { useEffect } from "react";

// QA F1-22 (QA-UI-042/094): leaving the campaign editor through the sidebar,
// a reload or closing the tab dropped unsaved edits without a word.
export const LEAVE_WITH_UNSAVED_CHANGES = "You have unsaved changes. Leave this page and lose them?";

function samePage(url: URL) {
    const path = (value: string) => value.replace(/\/+$/, "") || "/";
    return path(url.pathname) === path(window.location.pathname) && url.search === window.location.search;
}

/**
 * While `active`, asks before unsaved changes are discarded: the browser's own
 * prompt on reload or close, and a confirmation on in-app links (the sidebar,
 * breadcrumbs). A page's own leave buttons call {@link confirmLeave}.
 */
export function useUnsavedChangesGuard(active: boolean) {
    useEffect(() => {
        if (!active) return;
        const onBeforeUnload = (event: BeforeUnloadEvent) => {
            event.preventDefault();
            event.returnValue = "";
        };
        // Capture phase on the document runs before Next's <Link> handler.
        const onClick = (event: MouseEvent) => {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            const anchor = event.target instanceof Element ? event.target.closest("a[href]") as HTMLAnchorElement | null : null;
            if (!anchor || (anchor.target && anchor.target !== "_self") || anchor.hasAttribute("download")) return;
            const next = new URL(anchor.href, window.location.href);
            // Another site is left through a full unload, which the prompt above covers.
            if (next.origin !== window.location.origin || samePage(next)) return;
            if (window.confirm(LEAVE_WITH_UNSAVED_CHANGES)) return;
            event.preventDefault();
            event.stopPropagation();
        };
        window.addEventListener("beforeunload", onBeforeUnload);
        document.addEventListener("click", onClick, true);
        return () => {
            window.removeEventListener("beforeunload", onBeforeUnload);
            document.removeEventListener("click", onClick, true);
        };
    }, [active]);
}

/** For a page's own Cancel/Back controls: true when it may leave. */
export function confirmLeave(active: boolean) {
    return !active || window.confirm(LEAVE_WITH_UNSAVED_CHANGES);
}
