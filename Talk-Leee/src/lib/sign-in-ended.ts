/**
 * Per-tab memory of "this tab had a signed-in visitor", so the sign-in page can
 * say once that the sign-in ended. Stored in sessionStorage only; blocked
 * storage means no line, never an exception.
 *
 * Values: "signed-in" | "ended:<ms>". A tab that never held a sign-in, or whose
 * visitor signed out themselves, never reaches "ended".
 */
const KEY = "talky.signin.tab";
const SIGNED_IN = "signed-in";
const ENDED_PREFIX = "ended:";
const MAX_AGE_MS = 30_000;

function read(): string | null {
    try { return window.sessionStorage.getItem(KEY); } catch { return null; }
}

export function rememberSignedIn(): void {
    try { window.sessionStorage.setItem(KEY, SIGNED_IN); } catch { /* Storage blocked: no line. */ }
}

export function forgetSignIn(): void {
    try { window.sessionStorage.removeItem(KEY); } catch { /* Storage blocked: no line. */ }
}

function onSignInPage(): boolean {
    const path = window.location.pathname;
    return path === "/auth/login" || path.startsWith("/auth/login/");
}

export function noteSignInEnded(): void {
    try {
        if (read() !== SIGNED_IN) return;
        // Already on the sign-in page: the page has read the marker by now, so a
        // mark written here would only surface on a later reload. Nothing is left
        // to explain; the tab simply forgets that it held a sign-in.
        if (onSignInPage()) { window.sessionStorage.removeItem(KEY); return; }
        window.sessionStorage.setItem(KEY, `${ENDED_PREFIX}${Date.now()}`);
    } catch { /* Storage blocked: no line. */ }
}

export function takeSignInEnded(): boolean {
    try {
        const value = read();
        if (value === null || !value.startsWith(ENDED_PREFIX)) return false;
        window.sessionStorage.removeItem(KEY);
        const at = Number(value.slice(ENDED_PREFIX.length));
        const age = Date.now() - at;
        return Number.isFinite(at) && age >= 0 && age < MAX_AGE_MS;
    } catch {
        return false;
    }
}
