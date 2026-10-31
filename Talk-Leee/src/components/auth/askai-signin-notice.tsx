"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";

// Session-scoped flag written by the homepage Ask AI button when a signed-out
// user clicks it. sessionStorage, not a query param: a param would show the
// notice to anyone opening a copied link; sessionStorage cannot leave the tab.
export const ASKAI_SIGNIN_FLAG = "talky.askai.signin";

export function clearAskAiSignInFlag() {
    try {
        sessionStorage.removeItem(ASKAI_SIGNIN_FLAG);
    } catch { /* storage unavailable — nothing to clear */ }
}

/** True only when this tab arrived at the auth pages via the Ask AI button. */
export function hasAskAiSignInFlag(): boolean {
    try {
        return sessionStorage.getItem(ASKAI_SIGNIN_FLAG) === "1";
    } catch {
        return false;
    }
}

// Solid red, identical in both themes; white text. Inline so no theme
// variant can override it.
const NOTICE_BACKGROUND = "#dc2626";
const NOTICE_FOREGROUND = "#ffffff";

/**
 * Banner shown at the top of the auth column when the user was sent to
 * login/register by the Ask AI button. Persists across refresh and across
 * login↔register navigation; there is no dismiss control, so the flag clears
 * only on successful auth (the success handlers read it, then call
 * clearAskAiSignInFlag() before navigating away).
 *
 * Layout: the banner hugs its text and is centred above the card. The text is
 * two non-breaking halves inside a wrapping flex box. One rule decides the
 * line count, at any width: when the one-line width (both halves, the gap
 * between them and the horizontal padding) exceeds the column's content
 * width, the box switches to `min-content`, which puts each half on its own
 * centred line and shrinks the box to the longer half — so the banner never
 * reaches the column edge and never carries empty space either side of the
 * words. The column itself keeps the page's padding from the window edges.
 */
export function AskAiSignInNotice() {
    const [visible, setVisible] = useState(false);
    const boxRef = useRef<HTMLDivElement | null>(null);

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

    useLayoutEffect(() => {
        const box = boxRef.current;
        const column = box?.parentElement;
        if (!visible || !box || !column) return;
        const halves = box.querySelectorAll<HTMLElement>("[data-half]");
        if (halves.length !== 2 || typeof ResizeObserver === "undefined") return;

        const update = () => {
            const style = getComputedStyle(box);
            const oneLineWidth =
                halves[0].getBoundingClientRect().width +
                parseFloat(style.columnGap) +
                halves[1].getBoundingClientRect().width +
                parseFloat(style.paddingLeft) +
                parseFloat(style.paddingRight);
            const available = column.clientWidth;
            // Written to the DOM, not to React state: this is a measurement
            // of the current layout, and a state round-trip would paint one
            // frame with the wrong line count first.
            if (oneLineWidth > available) box.setAttribute("data-two-lines", "1");
            else box.removeAttribute("data-two-lines");
        };
        update();
        const observer = new ResizeObserver(update);
        observer.observe(column);
        // Font loading changes the halves' widths without resizing the column.
        halves.forEach((half) => observer.observe(half));
        return () => observer.disconnect();
    }, [visible]);

    if (!visible) return null;

    return (
        <>
            {/* When the notice is present and vertical space is tight, reclaim
                the branding block's 2rem bottom gap (to 0.5rem) and tighten
                the notice's own gap, so the sign-in page does not gain a
                scrollbar. Keyed off viewport height only — no device
                targeting — and emitted only while the notice is mounted, so
                every other arrival renders the page unchanged. */}
            <style>{`@media (max-height: 700px) { .askai-signin-notice { margin-bottom: 0.5rem !important; } .askai-signin-notice + div { margin-bottom: 0.5rem !important; } } .askai-signin-notice[data-two-lines="1"] { width: min-content; }`}</style>
            <div
                ref={boxRef}
                role="status"
                className="askai-signin-notice flex flex-wrap justify-center w-fit max-w-full mx-auto text-center rounded-lg shadow-sm"
                style={{
                    backgroundColor: NOTICE_BACKGROUND,
                    color: NOTICE_FOREGROUND,
                    // Fluid, breakpoint-free: 13px text on a 320px phone up to
                    // 14px (text-sm, matching the in-card alerts) above ~500px;
                    // vertical padding and the gap to the branding block track
                    // viewport height so the banner stays slim when short.
                    fontSize: "clamp(0.8125rem, 0.7rem + 0.5vw, 0.875rem)",
                    lineHeight: 1.4,
                    padding: "clamp(0.4rem, 1.2vh, 0.625rem) clamp(0.625rem, 2.5vw, 0.875rem)",
                    marginBottom: "clamp(0.625rem, 2vh, 1.5rem)",
                    // One ordinary word space between the halves on one line.
                    columnGap: "0.25em",
                }}
            >
                <span data-half className="whitespace-nowrap">Sign in to talk</span>
                {" "}
                <span data-half className="whitespace-nowrap">to the AI agent</span>
            </div>
        </>
    );
}
