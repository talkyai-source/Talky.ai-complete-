// Contract shared with Admin's cookie-changing requests. Browser locks are
// scoped to the frontend origin; this does not coordinate different origins.
export function sessionLockName(baseUrl: string): string {
    const url = new URL(baseUrl, window.location.origin);
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.search || url.hash) {
        throw new Error("Invalid session API URL");
    }
    return `talky-admin-session:${url.origin}${url.pathname.replace(/\/+$/, "")}`;
}

export class SessionCoordinationError extends Error {
    readonly sessionRequestNotSent = true;
    constructor(readonly code: string, message: string) { super(message); }
}

export async function withBrowserSessionLock<T>(baseUrl: string, run: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    // Server-side fetch has no shared browser cookie jar. This is an execution
    // environment boundary, never a browser capability or development bypass.
    if (typeof window === "undefined") return run();
    const acquisition = new AbortController();
    const onAbort = () => acquisition.abort();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let admitted = false;
    let expired = false;
    try {
        if (signal?.aborted) acquisition.abort();
        else signal?.addEventListener("abort", onAbort, { once: true });
        const locks = window.navigator.locks;
        if (!locks || typeof locks.request !== "function") throw new Error("Web Locks unavailable");
        const name = sessionLockName(baseUrl);
        timeout = setTimeout(() => { expired = true; acquisition.abort(); }, 20_000);
        return await locks.request(name, { signal: acquisition.signal }, () => {
            admitted = true;
            clearTimeout(timeout);
            return run();
        });
    } catch (error) {
        // After admission, a transport may have committed. Never label it unsent.
        if (admitted) throw error;
        throw new SessionCoordinationError(expired ? "SESSION_COORDINATION_TIMEOUT"
            : signal?.aborted ? "SESSION_REQUEST_CANCELLED" : "SESSION_COORDINATION_UNAVAILABLE",
        expired ? "The session request was not sent because another browser session update is still running. Wait for it to finish, then try again."
            : signal?.aborted ? "The session request was cancelled before it was sent."
                : "The session request was not sent because this page cannot coordinate browser sessions. Open it over HTTPS in a supported browser with site storage enabled, then try again.");
    } finally {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", onAbort);
    }
}
