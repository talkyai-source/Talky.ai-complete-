import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "./api";
import { ApiClientError, clearFreshLoginGrace, resetSessionExpiredLatch,
    setRequestIdentity, setSessionExpiredHandler, setTokenProvider } from "./http-client";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    setSessionExpiredHandler(null);
    setTokenProvider(null);
    setRequestIdentity(null);
    clearFreshLoginGrace();
});

for (const operation of ["change-password", "disable-mfa"] as const) {
    for (const status of [400, 401]) {
        test(`${operation} confirmation rejection ${status} stays inline and retains the session`, async () => {
            let token: string | null = "synthetic-session";
            let expired = 0;
            const paths: string[] = [];
            setTokenProvider(() => token, value => { token = value; });
            setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
            setSessionExpiredHandler(() => { expired++; });
            resetSessionExpiredLatch();
            clearFreshLoginGrace();
            globalThis.fetch = (async url => {
                paths.push(new URL(String(url)).pathname);
                return Response.json({ error: { code: "current_password_invalid",
                    message: "Current password is incorrect.", request_id: "synthetic-request" } }, { status });
            }) as typeof fetch;
            await assert.rejects(() => operation === "change-password"
                ? api.changePassword("wrong-current", "orchid lantern cobalt 2026!")
                : api.disableMfa("wrong-current"), (error: unknown) => {
                assert.ok(error instanceof ApiClientError);
                assert.equal(error.message, "Current password is incorrect.");
                return true;
            });
            assert.equal(expired, 0);
            assert.equal(token, "synthetic-session");
            assert.equal(paths.length, 1, "confirmation rejection must not refresh the login");
        });
    }
}

function setupRecoveryRegeneration(respond: (path: string) => Response) {
    let token: string | null = "synthetic-session";
    let expired = 0;
    const paths: string[] = [];
    setTokenProvider(() => token, value => { token = value; });
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    setSessionExpiredHandler(() => { expired++; });
    resetSessionExpiredLatch();
    clearFreshLoginGrace();
    globalThis.fetch = (async url => {
        const path = new URL(String(url)).pathname;
        paths.push(path);
        return respond(path);
    }) as typeof fetch;
    return { paths, expired: () => expired, token: () => token };
}

test("regenerate-recovery-codes 401 from an older server stays inline and keeps the session", async () => {
    const state = setupRecoveryRegeneration(path => path.endsWith("/auth/refresh")
        ? Response.json({ access_token: "renewed-token", user_id: "user-a", tenant_id: "tenant-a" })
        : Response.json({ error: { code: "unauthorized", message: "Invalid TOTP code.",
            request_id: "synthetic-request" } }, { status: 401 }));
    await assert.rejects(() => api.regenerateRecoveryCodes("000000"), (error: unknown) => {
        assert.ok(error instanceof ApiClientError);
        assert.equal(error.message, "Invalid TOTP code.");
        return true;
    });
    assert.equal(state.expired(), 0);
    assert.notEqual(state.token(), null);
});

test("regenerate-recovery-codes 400 mfa_code_invalid stays inline", async () => {
    const state = setupRecoveryRegeneration(() => Response.json({ error: { code: "mfa_code_invalid",
        message: "That code is not correct. Check your authenticator app and try again.",
        request_id: "synthetic-request" } }, { status: 400 }));
    await assert.rejects(() => api.regenerateRecoveryCodes("000000"), (error: unknown) => {
        assert.ok(error instanceof ApiClientError);
        assert.equal(error.code, "mfa_code_invalid");
        assert.equal(error.message, "That code is not correct. Check your authenticator app and try again.");
        return true;
    });
    assert.equal(state.paths.length, 1);
    assert.equal(state.paths.filter(p => p.endsWith("/auth/refresh")).length, 0);
    assert.equal(state.expired(), 0);
    assert.equal(state.token(), "synthetic-session");
});
