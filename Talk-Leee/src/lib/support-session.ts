/*
 * Where the open support conversation id lives between reloads: sessionStorage,
 * keyed by tenant and user so a different sign-in in the same tab never
 * resumes someone else's conversation. Pure functions; the component in
 * components/support/support-session.tsx decides when to clear.
 */

const PREFIX = "talklee.support.conversation.v1:";

export type SupportIdentity = { tenantId: string; userId: string };

export function supportSessionKey(identity: SupportIdentity): string {
    return `${PREFIX}${identity.tenantId}:${identity.userId}`;
}

function storage(): Storage | null {
    try {
        return typeof window === "undefined" ? null : window.sessionStorage;
    } catch {
        return null;
    }
}

export function readSupportConversation(identity: SupportIdentity): string | null {
    try {
        const value = storage()?.getItem(supportSessionKey(identity)) ?? null;
        return value && value.trim() ? value : null;
    } catch {
        return null;
    }
}

export function writeSupportConversation(identity: SupportIdentity, conversationId: string | null): void {
    try {
        const store = storage();
        if (!store) return;
        if (conversationId) store.setItem(supportSessionKey(identity), conversationId);
        else store.removeItem(supportSessionKey(identity));
    } catch {
        /* Storage can be unavailable; the conversation then lasts one page view. */
    }
}

/** Remove every stored support conversation, whoever it belonged to (sign-out). */
export function clearSupportConversations(): void {
    try {
        const store = storage();
        if (!store) return;
        const keys: string[] = [];
        for (let index = 0; index < store.length; index += 1) {
            const key = store.key(index);
            if (key && key.startsWith(PREFIX)) keys.push(key);
        }
        for (const key of keys) store.removeItem(key);
    } catch {
        /* Nothing to clear. */
    }
}
