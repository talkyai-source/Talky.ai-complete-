"use client";

import { useCallback, useEffect, useState } from "react";

// Session-scoped flag written by the homepage Ask AI button when a signed-out
// user clicks it. sessionStorage, not a query param: a param would show the
// notice to anyone opening a copied link; sessionStorage cannot leave the tab.
export const ASKAI_SIGNIN_FLAG = "talky.askai.signin";

export function clearAskAiSignInFlag() {
    try {
        sessionStorage.removeItem(ASKAI_SIGNIN_FLAG);
    } catch { /* storage unavailable — nothing to clear */ }
}

/**
 * Banner shown at the top of the auth column when the user was sent to
 * login/register by the Ask AI button. Persists across refresh and across
 * login↔register navigation; clears on dismiss or successful auth (the
 * success handlers call clearAskAiSignInFlag() before navigating away).
 */
export function AskAiSignInNotice() {
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        // Read in an effect, never during render: sessionStorage is
        // browser-only, and a render-time read would make the server and
        // client HTML disagree (hydration mismatch).
        try {
            if (sessionStorage.getItem(ASKAI_SIGNIN_FLAG) === "1") {
                // eslint-disable-next-line react-hooks/set-state-in-effect -- reads browser-only sessionStorage unavailable during SSR
                setVisible(true);
            }
        } catch { /* storage blocked (private mode) — never show the notice */ }
    }, []);

    const dismiss = useCallback(() => {
        clearAskAiSignInFlag();
        setVisible(false);
    }, []);

    if (!visible) return null;

    return (
        <>
            {/* When the notice is present and vertical space is tight, reclaim
                the branding block's 2rem bottom gap (to 0.5rem) and tighten
                the notice's own gap, so the sign-in page does not gain a
                scrollbar. Keyed off viewport height only — no device
                targeting — and emitted only while the notice is mounted, so
                every other arrival renders the page unchanged. */}
            <style>{`@media (max-height: 700px) { .askai-signin-notice { margin-bottom: 0.5rem !important; } .askai-signin-notice + div { margin-bottom: 0.5rem !important; } }`}</style>
            <div
                role="status"
                className="askai-signin-notice flex items-center bg-red-50 border border-red-200 text-red-600 rounded-lg shadow-sm"
                style={{
                    // Fluid, breakpoint-free: 13px text on a 320px phone up to
                    // 14px (text-sm, matching the in-card alerts) above ~500px;
                    // vertical padding and the gap to the branding block track
                    // viewport height so the banner stays slim when short.
                    fontSize: "clamp(0.8125rem, 0.7rem + 0.5vw, 0.875rem)",
                    lineHeight: 1.4,
                    padding: "clamp(0.4rem, 1.2vh, 0.625rem) clamp(0.625rem, 2.5vw, 0.875rem)",
                    marginBottom: "clamp(0.625rem, 2vh, 1.5rem)",
                }}
            >
                <span className="flex-1 text-left">Sign in to talk to the AI agent</span>
                <button
                    type="button"
                    onClick={dismiss}
                    aria-label="Dismiss"
                    className="shrink-0 inline-flex items-center justify-center opacity-70 hover:opacity-100"
                    // Fixed 44x44 effective tap target at every size: small
                    // glyph, padding-expanded hit box, negative margin so the
                    // box does not inflate the banner's height or width.
                    style={{
                        minWidth: 44,
                        minHeight: 44,
                        padding: 12,
                        margin: "-12px -10px -12px 0",
                        fontSize: "1rem",
                        lineHeight: 1,
                    }}
                >
                    ×
                </button>
            </div>
        </>
    );
}
