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

export function noteSignInEnded(): void {
    try {
        if (read() !== SIGNED_IN) return;
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
