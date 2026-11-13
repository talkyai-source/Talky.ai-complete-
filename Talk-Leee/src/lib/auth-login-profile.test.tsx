import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { useLayoutEffect } from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { clearFreshLoginGrace } from "@/lib/http-client";

// QA F1-32 (QA-UI-119): right after signing in, times were shown in the
// browser's zone: the login answer carries no account time zone, and the
// profile (/auth/me) was only read again on the next reload. A sign-in now
// reads the profile once, fenced to that sign-in.
const originalGetMe = api.getMe;
const originalLogout = api.logout;
type Me = Awaited<ReturnType<typeof api.getMe>>;
const profile = (id: string, tenant: string, zone: string): Me => ({
    id, tenant_id: tenant, email: `${id}@example.test`, role: "tenant_admin", minutes_remaining: 10, minutes_state: "known",
    account_timezone: zone,
} as Me);
let auth: ReturnType<typeof useAuth>;
function Probe() {
    const current = useAuth();
    useLayoutEffect(() => { auth = current; });
    return null;
}
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((yes) => { resolve = yes; });
    return { promise, resolve };
}
afterEach(() => {
    cleanup();
    api.getMe = originalGetMe;
    api.logout = originalLogout;
    window.localStorage.clear();
    clearFreshLoginGrace();
});

/** A signed-out provider (its bootstrap read finds no session). */
async function signedOut(reads: Array<ReturnType<typeof deferred<Me>>>) {
    let call = 0;
    api.getMe = () => {
        call += 1;
        if (call === 1) return Promise.reject(new Error("no session"));
        return reads[call - 2].promise;
    };
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => assert.equal(auth.status, "anonymous"));
}
function signIn(id: string, tenant: string) {
    act(() => { auth.applyLoginResult({ user_id: id, tenant_id: tenant, email: `${id}@example.test`, role: "tenant_admin" }); });
}

test("a sign-in reads the profile once, so the account time zone is known without a reload", async () => {
    const read = deferred<Me>();
    await signedOut([read]);
    signIn("user-a", "tenant-a");
    assert.equal(auth.user?.id, "user-a");
    assert.equal(auth.user?.account_timezone, undefined, "the login answer has no zone");
    await act(async () => { read.resolve(profile("user-a", "tenant-a", "Asia/Karachi")); await read.promise; });
    assert.equal(auth.user?.account_timezone, "Asia/Karachi");
});

test("a profile read that answers after a sign-out is discarded", async () => {
    const read = deferred<Me>();
    await signedOut([read]);
    api.logout = async () => undefined;
    signIn("user-a", "tenant-a");
    await act(async () => { await auth.logout(); });
    assert.equal(auth.user, null);
    await act(async () => { read.resolve(profile("user-a", "tenant-a", "Asia/Karachi")); await read.promise; });
    assert.equal(auth.user, null, "the signed-out tab did not come back");
});

test("a profile read for a superseded sign-in is discarded", async () => {
    const first = deferred<Me>();
    const second = deferred<Me>();
    await signedOut([first, second]);
    signIn("user-a", "tenant-a");
    signIn("user-b", "tenant-b");
    await act(async () => { first.resolve(profile("user-a", "tenant-a", "Asia/Karachi")); await first.promise; });
    assert.equal(auth.user?.id, "user-b", "the earlier sign-in's profile did not replace the later one");
    await act(async () => { second.resolve(profile("user-b", "tenant-b", "Europe/London")); await second.promise; });
    assert.equal(auth.user?.account_timezone, "Europe/London");
});

test("a profile for a different account than the sign-in is not applied", async () => {
    const read = deferred<Me>();
    await signedOut([read]);
    signIn("user-b", "tenant-b");
    await act(async () => { read.resolve(profile("user-a", "tenant-a", "Asia/Karachi")); await read.promise; });
    assert.equal(auth.user?.id, "user-b", "the tab was not switched to another account");
    assert.equal(auth.user?.account_timezone, undefined);
});
