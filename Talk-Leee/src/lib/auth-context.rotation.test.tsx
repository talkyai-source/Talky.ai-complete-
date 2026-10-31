import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { useEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { clearFreshLoginGrace } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

const originalGetMe = api.getMe;
const accountA = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
const accountB = { ...accountA, id: "user-b", tenant_id: "tenant-b", email: "b@example.test" };
let auth: ReturnType<typeof useAuth>;
// Mirrors the DashboardLayout redirect effect: it sends the user to /auth/login
// whenever a settled render has no user (`!authLoading && !user`).
let redirects = 0;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
function Probe() {
    const current = useAuth();
    useEffect(() => { auth = current; });
    useEffect(() => { if (!current.loading && !current.user) redirects += 1; }, [current.loading, current.user]);
    return <span>{current.user?.id ?? "anonymous"}</span>;
}
afterEach(() => {
    cleanup(); api.getMe = originalGetMe; window.localStorage.clear(); clearFreshLoginGrace(); notificationsStore.setIdentity(null);
});

test("a token rotated by another tab does not look like a signed-out session while /me verifies it", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-a");
    api.getMe = async () => ({ ...accountA });
    render(<AuthProvider><Probe /></AuthProvider>);
    await screen.findByText("user-a");
    redirects = 0;
    const verifying = deferred<typeof accountA>(); api.getMe = () => verifying.promise;
    act(() => {
        window.localStorage.setItem("talklee.auth.token", "synthetic-a-rotated");
        window.dispatchEvent(new window.StorageEvent("storage", { key: "talklee.auth.token", newValue: "synthetic-a-rotated", storageArea: window.localStorage }));
    });
    assert.equal(redirects, 0, "a settled `!loading && !user` render would bounce the tab to /auth/login");
    await act(async () => { verifying.resolve({ ...accountA }); await verifying.promise; });
    await waitFor(() => assert.equal(auth.user?.id, "user-a"));
    assert.equal(redirects, 0);
});

test("a token swapped to another account by another tab still masks the old user and never keeps it", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-a");
    api.getMe = async () => ({ ...accountA });
    render(<AuthProvider><Probe /></AuthProvider>);
    await screen.findByText("user-a");
    const verifying = deferred<typeof accountB>(); api.getMe = () => verifying.promise;
    act(() => {
        window.localStorage.setItem("talklee.auth.token", "synthetic-b");
        window.dispatchEvent(new window.StorageEvent("storage", { key: "talklee.auth.token", newValue: "synthetic-b", storageArea: window.localStorage }));
    });
    assert.equal(auth.user, null);
    await act(async () => { verifying.resolve(accountB); await verifying.promise; });
    await waitFor(() => assert.equal(auth.user?.id, "user-b"));
});
