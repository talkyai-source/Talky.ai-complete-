import { postAuthDashboard, safeInternalPath } from "@/lib/post-auth-navigation";

// The sign-up page follows a `next` address the way the sign-in page does: the
// check is safeInternalPath (reached through postAuthDashboard in
// register-client.tsx). Another site, "//", "/\", a backslash or control
// character, an encoded variant and any /auth/ page are refused there and the
// visitor lands on the dashboard. Nothing here checks an address on its own.

type SearchParamsLike = { get(name: string): string | null } | null | undefined;

/** The raw `next` value of the page's address. It is checked only where it is used. */
export function nextFromAddress(searchParams: SearchParamsLike): string | null {
    return searchParams?.get("next") ?? null;
}

/** The sign-in link of the sign-up page keeps a safe `next`, so a visitor who already has an account arrives at the same page. */
export function signInHref(next: string | null): string {
    const safe = safeInternalPath(next);
    return safe ? `/auth/login/?next=${encodeURIComponent(safe)}` : "/auth/login";
}

/** The sign-up link of the sign-in page keeps a safe `next`, so a visitor who chose "Sign in" and then "Create an account" keeps their plan. */
export function signUpHref(next: string | null): string {
    const safe = safeInternalPath(next);
    return safe ? `/auth/register/?next=${encodeURIComponent(safe)}` : "/auth/register/";
}

/**
 * Where the visitor goes once the account exists. An Ask AI arrival returns to
 * the homepage unless the role is white-label; everyone else follows the shared
 * check (a safe `next`, else their dashboard). `next` is only ever an argument
 * of postAuthDashboard here.
 */
export function destinationAfterSignup(role: string | null | undefined, next: string | null, fromAskAi: boolean): string {
    return fromAskAi && role !== "white_label_admin" ? "/" : postAuthDashboard(role, next);
}
