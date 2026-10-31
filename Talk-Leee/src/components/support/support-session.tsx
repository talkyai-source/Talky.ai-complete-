"use client";

import { useEffect, useMemo } from "react";
import { useAuth } from "@/lib/auth-context";
import { clearSupportConversations, type SupportIdentity } from "@/lib/support-session";
import { supportChatStore } from "@/lib/support-chat-store";

/*
 * The one bridge from verified authentication to the stored support
 * conversation, after NotificationsIdentityProvider's pattern: auth-context is
 * not touched. When the identity drops (sign-out, cross-tab logout, a rejected
 * session) every stored conversation id is removed and the drawer is reset, so
 * the next sign-in never resumes someone else's chat.
 */

export function useSupportIdentity(): SupportIdentity | null {
    const { user, status } = useAuth();
    const tenantId = user?.tenant_id;
    const userId = user?.id;
    return useMemo(
        () => (status === "authenticated" && tenantId && userId ? { tenantId, userId } : null),
        [status, tenantId, userId],
    );
}

export function SupportSessionGuard() {
    const { status } = useAuth();
    useEffect(() => {
        if (status !== "anonymous") return;
        clearSupportConversations();
        supportChatStore.reset();
        supportChatStore.setOpen(false);
    }, [status]);
    return null;
}
