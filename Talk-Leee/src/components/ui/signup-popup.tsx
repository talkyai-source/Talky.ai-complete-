"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { isPublicRoute } from "@/lib/public-routes";

// One timer for the whole page load. It starts when the root layout mounts
// and never restarts on client-side navigation; a full reload remounts the
// layout and so starts it again.
const SHOW_DELAY_MS = 10_000;

const REGISTER_PATH = "/auth/register";

/**
 * Bottom-right invitation to create an account. Mounted once in the root
 * layout (layout.tsx), so its state lives for the life of the page load and
 * nothing is persisted: no storage, no cookie, no server state.
 *
 * - Hidden until SHOW_DELAY_MS after first load; then shown on every page.
 * - Cancel hides it for the current page only. Any other page shows it at
 *   once; coming back to a cancelled page shows it SHOW_DELAY_MS after
 *   arrival. A page cancelled once keeps that wait for the rest of the load.
 * - Shown only on the public routes in src/lib/public-routes.ts (exact
 *   match), so signed-in pages, auth pages and anything new get nothing.
 * - Cancel is the only way to close it: no Escape, no outside click.
 */
export function SignupPopup() {
    const pathname = usePathname();
    const labelId = useId();
    const [armed, setArmed] = useState(false);
    // The pathname the popup is currently cleared to show on. Derived
    // visibility is `armed && shownFor === pathname`, so a navigation hides
    // it in the same render and the effect below re-clears it for the new page.
    const [shownFor, setShownFor] = useState<string | null>(null);
    // Pages cancelled during this page load. Read in the effect and the
    // handler only, never during render.
    const cancelledPages = useRef<Set<string>>(new Set());

    useEffect(() => {
        // Counted from the page load's navigation start (performance.now() is
        // the time since it), not from hydration, so the popup lands at 10 s
        // after load however long the page took to become interactive.
        const timer = setTimeout(() => setArmed(true), Math.max(0, SHOW_DELAY_MS - performance.now()));
        return () => clearTimeout(timer);
    }, []);

    useEffect(() => {
        if (!armed || !isPublicRoute(pathname)) return;
        // 0 for a page never cancelled (shows on arrival), the full wait for
        // one cancelled earlier. The timer is cleared if the visitor leaves
        // before it fires, so the wait is always counted from arrival.
        const delay = cancelledPages.current.has(pathname) ? SHOW_DELAY_MS : 0;
        const timer = setTimeout(() => setShownFor(pathname), delay);
        return () => clearTimeout(timer);
    }, [armed, pathname]);

    const cancel = useCallback(() => {
        cancelledPages.current.add(pathname);
        setShownFor(null);
    }, [pathname]);

    if (!armed || !isPublicRoute(pathname) || shownFor !== pathname) return null;

    return (
        <Card
            role="region"
            aria-labelledby={labelId}
            data-signup-popup=""
            className="fixed bottom-4 right-4 z-[400] w-[min(calc(100%-2rem),24rem)] p-4"
        >
            <p id={labelId} className="text-sm leading-relaxed">
                Ready to make your first call? Create an account to configure your AI voice agent.
            </p>
            <div className="mt-3 flex flex-row flex-nowrap items-center justify-center gap-3">
                <Button
                    asChild
                    className="bg-indigo-600 text-white hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400"
                >
                    <Link href={REGISTER_PATH}>Signup</Link>
                </Button>
                <Button type="button" variant="destructive" onClick={cancel}>
                    Cancel
                </Button>
            </div>
        </Card>
    );
}
