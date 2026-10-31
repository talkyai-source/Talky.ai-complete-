/*
 * What the sidebar needs to know about support, published by the launcher in
 * the dashboard layout: whether the Support entry exists and its unread
 * count. A plain store keeps the sidebar free of the config and unread
 * queries, so it renders unchanged wherever those providers are absent.
 */

export type SupportStatus = { visible: boolean; unreadCount: number };

const DEFAULT: SupportStatus = { visible: false, unreadCount: 0 };

type Listener = () => void;
const listeners = new Set<Listener>();
let current: SupportStatus = DEFAULT;

export function publishSupportStatus(next: SupportStatus): void {
    if (next.visible === current.visible && next.unreadCount === current.unreadCount) return;
    current = next;
    for (const listener of listeners) listener();
}

export function subscribeSupportStatus(listener: Listener): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
}

export function getSupportStatus(): SupportStatus {
    return current;
}

export function getServerSupportStatus(): SupportStatus {
    return DEFAULT;
}

export function resetSupportStatusForTests(): void {
    current = DEFAULT;
    listeners.clear();
}
