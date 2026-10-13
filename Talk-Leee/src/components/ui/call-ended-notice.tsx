"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";

// Session-scoped flag written by the homepage Ask AI popup when route
// navigation unmounts it mid-call (the call is homepage-only; dropping it on
// navigation is by design). sessionStorage for the same reason as
// ASKAI_SIGNIN_FLAG: it cannot leave the tab or show up in a copied link.
export const ASKAI_CALL_ENDED_FLAG = "talky.askai.callended";

/**
 * Small dismissible toast telling the user their Ask AI call ended because
 * they left the homepage. Mounted once in the root layout so it renders on
 * whatever page the navigation lands on. The flag is consumed on first
 * read, so the notice shows once per dropped call and never on a page the
 * user reaches without a live call having been up.
 */
export function CallEndedNotice() {
    const [visible, setVisible] = useState(false);
    // This component lives in the root layout, which persists across SPA
    // navigations — it mounts once per tab. The flag check must therefore
    // re-run on every route change, not just on mount: the popup writes the
    // flag in its unmount cleanup, which runs in the same commit that
    // changes the pathname, before this effect fires.
    const pathname = usePathname();

    useEffect(() => {
        // Read in an effect, never during render: sessionStorage is
        // browser-only, and a render-time read would make the server and
        // client HTML disagree (hydration mismatch).
        try {
            if (sessionStorage.getItem(ASKAI_CALL_ENDED_FLAG) === "1") {
                sessionStorage.removeItem(ASKAI_CALL_ENDED_FLAG);
                // eslint-disable-next-line react-hooks/set-state-in-effect -- reads browser-only sessionStorage unavailable during SSR
                setVisible(true);
            }
        } catch { /* storage blocked (private mode) — never show the notice */ }
    }, [pathname]);

    const dismiss = useCallback(() => setVisible(false), []);

    if (!visible) return null;

    return (
        <div role="status" className="askAiCallEndedNotice">
            <span>Your Ask AI call ended when you left the page.</span>
            <button
                type="button"
                onClick={dismiss}
                aria-label="Dismiss"
                className="askAiCallEndedDismiss"
            >
                <X width={16} height={16} aria-hidden />
            </button>
        </div>
    );
}
