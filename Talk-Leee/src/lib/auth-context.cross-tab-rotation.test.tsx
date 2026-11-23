import { afterEach, test } from "node:test";
import { useEffect } from "react";
import assert from "node:assert/strict";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api, sharedHttpClient } from "@/lib/api";
import { adoptSiblingTabRotation, clearFreshLoginGrace, isApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

// A renewal in one tab writes the new token to localStorage; every other tab
// gets a `storage` event. For the SAME user, tenant and session that is a
// rotation, not an account change.

const originalGetMe = api.getMe;
const originalFetch = globalThis.fetch;
const accountA = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
let auth: ReturnType<typeof useAuth>;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
function Probe() {
    const current = useAuth();
    useEffect(() => { auth = current; });
    return <span>{current.user?.id ?? "anonymous"}</span>;
}
function jwt(claims: Record<string, unknown>) {
    const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
    return `${part({ alg: "HS256", typ: "JWT" })}.${part(claims)}.synthetic-signature`;
}
const tokenA1 = jwt({ sub: "user-a", tenant_id: "tenant-a", sid: "session-1", n: 1 });
const tokenA2 = jwt({ sub: "user-a", tenant_id: "tenant-a", sid: "session-1", n: 2 });
afterEach(() => {
    cleanup(); api.getMe = originalGetMe; globalThis.fetch = originalFetch;
    window.localStorage.clear(); clearFreshLoginGrace(); notificationsStore.setIdentity(null);
});
function otherTabWrites(token: string | null) {
    act(() => {
        if (token) window.localStorage.setItem("talklee.auth.token", token); else window.localStorage.removeItem("talklee.auth.token");
        window.dispatchEvent(new window.StorageEvent("storage", { key: "talklee.auth.token", newValue: token, storageArea: window.localStorage }));
    });
}
async function signedInAs(token: string) {
    window.localStorage.setItem("talklee.auth.token", token);
    api.getMe = async () => ({ ...accountA });
    render(<AuthProvider><Probe /></AuthProvider>);
    await screen.findByText("user-a");
}
function stubFetch() {
    const sent: Array<string | null> = [];
    const gate = deferred<Response>();
    let gated = true;
    globalThis.fetch = (async (_url, init) => {
        sent.push(new Headers(init?.headers).get("authorization"));
        if (gated) { gated = false; return gate.promise; }
        return Response.json({ ok: true });
    }) as typeof fetch;
    return { sent, gate };
}

test("a renewed token for the same user, tenant and session keeps an in-flight request and the next one uses it", async () => {
    await signedInAs(tokenA1);
    const { sent, gate } = stubFetch();
    const inFlight = sharedHttpClient().request({ path: "/campaigns", method: "POST", body: { name: "x" } });
    await waitFor(() => assert.equal(sent.length, 1));
    otherTabWrites(tokenA2);
    assert.equal(auth.user?.id, "user-a", "a rotation must not mask the signed-in user");
    assert.equal(auth.loading, false, "a rotation must not start a sign-in re-check");
    gate.resolve(Response.json({ created: true }));
    assert.deepEqual(await inFlight, { created: true });
    await sharedHttpClient().request({ path: "/campaigns", method: "POST", body: { name: "y" } });
    assert.equal(sent[1], `Bearer ${tokenA2}`);
    assert.equal(auth.accessToken, tokenA2);
});

test("a token for another user still fails the in-flight request and masks the signed-in user", async () => {
    await signedInAs(tokenA1);
    const { sent, gate } = stubFetch();
    const verifying = deferred<typeof accountA>(); 
    const inFlight = sharedHttpClient().request({ path: "/campaigns", method: "POST", body: { name: "x" } });
    await waitFor(() => assert.equal(sent.length, 1));
    api.getMe = () => verifying.promise;
    otherTabWrites(jwt({ sub: "user-b", tenant_id: "tenant-a", sid: "session-1" }));
    assert.equal(auth.user, null);
    gate.resolve(Response.json({ created: true }));
    await assert.rejects(inFlight, (err: unknown) => isApiClientError(err) && err.code === "identity_changed");
    await act(async () => { verifying.resolve({ ...accountA }); await verifying.promise; });
});

test("a token for another tenant or another session is not a rotation", async () => {
    for (const claims of [{ sub: "user-a", tenant_id: "tenant-b", sid: "session-1" }, { sub: "user-a", tenant_id: "tenant-a", sid: "session-2" }]) {
        await signedInAs(tokenA1);
        const verifying = deferred<typeof accountA>(); api.getMe = () => verifying.promise;
        otherTabWrites(jwt(claims));
        assert.equal(auth.user, null, JSON.stringify(claims));
        await act(async () => { verifying.resolve({ ...accountA }); await verifying.promise; });
        cleanup(); window.localStorage.clear();
    }
});

test("a token whose identity cannot be read is not a rotation", async () => {
    await signedInAs(tokenA1);
    const verifying = deferred<typeof accountA>(); api.getMe = () => verifying.promise;
    otherTabWrites("not-a-jwt");
    assert.equal(auth.user, null);
    await act(async () => { verifying.resolve({ ...accountA }); await verifying.promise; });
});

test("a removed token still signs this tab out and fails the in-flight request", async () => {
    await signedInAs(tokenA1);
    const { sent, gate } = stubFetch();
    const inFlight = sharedHttpClient().request({ path: "/campaigns", method: "POST", body: { name: "x" } });
    await waitFor(() => assert.equal(sent.length, 1));
    otherTabWrites(null);
    assert.equal(auth.user, null);
    assert.equal(auth.accessToken, null);
    gate.resolve(Response.json({ created: true }));
    await assert.rejects(inFlight, (err: unknown) => isApiClientError(err) && err.code === "identity_changed");
});

// The Test agent renews the sign-in itself (campaigns/test-agent-button.tsx) and stores the
// new token. These two cases pin why it must also record the renewal.
test("a renewal this tab stores WITHOUT recording it fails a request already in flight", async () => {
    await signedInAs(tokenA1);
    const { sent, gate } = stubFetch();
    const inFlight = sharedHttpClient().request({ path: "/campaigns", method: "POST", body: { name: "x" } });
    await waitFor(() => assert.equal(sent.length, 1));
    act(() => { sharedHttpClient().setToken(tokenA2); });
    gate.resolve(Response.json({ created: true }));
    await assert.rejects(inFlight, (error: unknown) => isApiClientError(error) && error.code === "identity_changed");
});

test("the same renewal, recorded as one first, lets the request in flight finish", async () => {
    await signedInAs(tokenA1);
    const { sent, gate } = stubFetch();
    const inFlight = sharedHttpClient().request({ path: "/campaigns", method: "POST", body: { name: "x" } });
    await waitFor(() => assert.equal(sent.length, 1));
    act(() => { adoptSiblingTabRotation(tokenA1, tokenA2); sharedHttpClient().setToken(tokenA2); });
    gate.resolve(Response.json({ created: true }));
    assert.deepEqual(await inFlight, { created: true });
});
