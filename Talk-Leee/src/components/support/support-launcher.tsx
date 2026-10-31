"use client";

import { useCallback, useEffect } from "react";
import { usePathname } from "next/navigation";
import { HelpButton } from "@/components/support/help-button";
import { SupportDrawer } from "@/components/support/support-drawer";
import { SupportSessionGuard, useSupportIdentity } from "@/components/support/support-session";
import { SupportUnreadNotifier } from "@/components/support/support-unread-notifier";
import { useSupportDrawerState } from "@/components/support/use-support-chat";
import { commitSha } from "@/lib/env";
import { useSupportConfig } from "@/lib/support-config";
import { supportChatStore } from "@/lib/support-chat-store";
import { supportPageContext } from "@/lib/support-page-context";
import { publishSupportStatus } from "@/lib/support-status-store";
import { useSupportUnread } from "@/lib/support-unread";

/**
 * The one mount point in the dashboard layout. Renders nothing at all until
 * GET /support/config says support is on and the user may report; then the
 * Help button, the drawer, the unread notifier and the session guard.
 */
export function SupportLauncher() {
    const gate = useSupportConfig();
    const identity = useSupportIdentity();
    const pathname = usePathname();
    const { open } = useSupportDrawerState();
    const { count } = useSupportUnread();
    const visible = gate.config !== null && gate.canReport;
    // The sidebar reads support state from a store so it needs no query provider.
    useEffect(() => {
        publishSupportStatus({ visible, unreadCount: visible ? count : 0 });
    }, [visible, count]);
    // A link inside the drawer (a docs source, a related campaign, the new ticket)
    // navigates away; the drawer closes with the page it was opened on.
    useEffect(() => {
        supportChatStore.setOpen(false);
    }, [pathname]);
    const pageContext = useCallback(() => supportPageContext({
        pathname,
        commitSha: commitSha(),
        viewport: typeof window === "undefined" ? undefined : { width: window.innerWidth, height: window.innerHeight },
    }), [pathname]);

    if (!visible || !gate.config) return <SupportSessionGuard />;
    return (
        <>
            <SupportSessionGuard />
            <SupportUnreadNotifier />
            <HelpButton open={open} onClick={() => supportChatStore.setOpen(true)} />
            <SupportDrawer config={gate.config} identity={identity} pageContext={pageContext} />
        </>
    );
}
