// Tab-scoped marker: "the server ended this person's sign-in". Written when the
// session-expired redirect to the sign-in page happens, read (and removed) once
// by the sign-in page. sessionStorage, not a query parameter: a parameter would
// let another site open the sign-in page with the notice switched on; the words
// of the notice live in the page, the marker only switches the line on.
export const SIGNIN_ENDED_FLAG = "talky.signin.ended";

// A marker nobody read (the redirect never reached the page) must not show up
// on a much later, ordinary visit.
const SIGNIN_ENDED_MAX_AGE_MS = 5 * 60 * 1000;

export function markSignInEnded() {
    try { sessionStorage.setItem(SIGNIN_ENDED_FLAG, String(Date.now())); } catch { /* storage unavailable: no notice */ }
}

/** True once after the marker was written; the marker is removed by the read. */
export function takeSignInEnded(): boolean {
    try {
        const raw = sessionStorage.getItem(SIGNIN_ENDED_FLAG);
        if (raw === null) return false;
        sessionStorage.removeItem(SIGNIN_ENDED_FLAG);
        const at = Number(raw);
        return Number.isFinite(at) && Date.now() - at >= 0 && Date.now() - at < SIGNIN_ENDED_MAX_AGE_MS;
    } catch {
        return false;
    }
}
