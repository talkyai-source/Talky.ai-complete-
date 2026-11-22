// Who a sign-in token belongs to, read from its (unverified) claims: user
// (`sub`), tenant (`tenant_id`, null for a tenantless account) and session
// (`sid`). Used only to tell a sibling tab's routine token renewal from an
// account change; the server still verifies every token it receives.
export type TokenIdentity = { userId: string; tenantId: string | null; sessionId: string };

export function readTokenIdentity(token: string | null | undefined): TokenIdentity | null {
    if (!token) return null;
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    try {
        const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
        const claims = JSON.parse(atob(b64.padEnd(Math.ceil(b64.length / 4) * 4, "="))) as Record<string, unknown>;
        const { sub, tenant_id: tenant, sid } = claims;
        if (typeof sub !== "string" || !sub || typeof sid !== "string" || !sid) return null;
        if (tenant !== null && typeof tenant !== "string") return null;
        return { userId: sub, tenantId: tenant || null, sessionId: sid };
    } catch {
        return null;
    }
}

/** True only when both identities were read and name the same user, tenant and session. */
export function isSameSignIn(a: TokenIdentity | null, b: TokenIdentity | null): boolean {
    return !!a && !!b && a.userId === b.userId && a.tenantId === b.tenantId && a.sessionId === b.sessionId;
}
