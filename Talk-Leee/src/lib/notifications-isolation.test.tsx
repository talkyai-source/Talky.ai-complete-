import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { useLayoutEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { AUTH_IDENTITY_EVENT_KEY, AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { clearFreshLoginGrace, createHttpClient, ApiClientError } from "@/lib/http-client";
import { defaultNotificationsSettings, notificationsStore, notificationScopeKey } from "@/lib/notifications";
import { NotificationsIdentityProvider, useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";

const originalGetMe = api.getMe;
const originalLogout = api.logout;
const originalFetch = globalThis.fetch;
const originalFallback = process.env.NEXT_PUBLIC_BEARER_FALLBACK;
const originalStorageDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage")!;
const originalBroadcastDescriptor = Object.getOwnPropertyDescriptor(window, "BroadcastChannel");
const accountA = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
const accountB = { ...accountA, id: "user-b", tenant_id: "tenant-b", email: "b@example.test" };
let auth: ReturnType<typeof useAuth>;
let actions: ReturnType<typeof useNotificationsActions>;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
function CurrentNotifications() {
    const currentAuth = useAuth();
    const currentActions = useNotificationsActions();
    useLayoutEffect(() => { auth = currentAuth; actions = currentActions; });
    const state = useNotificationsState();
    return <div><span>{currentAuth.user?.id ?? "anonymous"}</span><span>{state.scopeKey ?? "unscoped"}</span>{state.notifications.map(item => <p key={item.id}>{item.title}</p>)}<span>{state.settings.integrations.webhook.url || "No destination"}</span></div>;
}
function mount() { return render(<AuthProvider><NotificationsIdentityProvider /><CurrentNotifications /></AuthProvider>); }
async function verifiedA() {
    window.localStorage.setItem("talklee.auth.token", "synthetic-a");
    api.getMe = async () => ({ ...accountA });
    mount();
    await waitFor(() => assert.equal(notificationsStore.getSnapshot().scopeKey, notificationScopeKey({ tenantId: "tenant-a", userId: "user-a" })));
}
function emitStorage(key: string | null, newValue: string | null) {
    if (key) { if (newValue === null) window.localStorage.removeItem(key); else window.localStorage.setItem(key, newValue); }
    window.dispatchEvent(new window.StorageEvent("storage", { key, newValue, storageArea: window.localStorage }));
}
function blockStorage() {
    Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new Error("synthetic storage blocked"); } });
}
function installBroadcastChannel() {
    const channels = new Set<Channel>();
    class Channel {
        onmessage: ((event: { data: unknown }) => void) | null = null;
        constructor(public name: string) { channels.add(this); }
        postMessage(data: unknown) {
            for (const other of channels) if (other !== this && other.name === this.name) queueMicrotask(() => {
                if (channels.has(other)) other.onmessage?.({ data });
            });
        }
        close() { channels.delete(this); }
    }
    Object.defineProperty(window, "BroadcastChannel", { configurable: true, writable: true, value: Channel });
    return { Channel, channels };
}
afterEach(() => {
    cleanup(); notificationsStore.setIdentity(null); api.getMe = originalGetMe; api.logout = originalLogout;
    globalThis.fetch = originalFetch;
    Object.defineProperty(window, "localStorage", originalStorageDescriptor);
    if (originalBroadcastDescriptor) Object.defineProperty(window, "BroadcastChannel", originalBroadcastDescriptor);
    else Reflect.deleteProperty(window, "BroadcastChannel");
    window.localStorage.clear(); clearFreshLoginGrace();
    if (originalFallback === undefined) delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    else process.env.NEXT_PUBLIC_BEARER_FALLBACK = originalFallback;
});

test("AuthProvider pins verified cookie-only tenant on writes and blocks them during account verification", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    api.getMe = async () => ({ ...accountA }); mount(); await screen.findByText("user-a");
    const client = createHttpClient({ baseUrl: "http://example.test" });
    const seen: Headers[] = [];
    globalThis.fetch = (async (_url, init) => { seen.push(new Headers(init?.headers)); return Response.json({ saved: true }); }) as typeof fetch;
    await client.request({ path: "/campaigns", method: "POST" });
    assert.equal(seen[0].get("X-Talky-Expected-User"), "user-a");
    assert.equal(seen[0].get("X-Talky-Expected-Tenant"), "tenant-a");
    const next = deferred<typeof accountB>(); api.getMe = () => next.promise;
    act(() => { emitStorage(AUTH_IDENTITY_EVENT_KEY, JSON.stringify({ kind: "changed", nonce: "cp08-cookie-switch" })); });
    assert.equal(auth.user, null);
    await assert.rejects(client.request({ path: "/campaigns", method: "POST" }),
        (error: unknown) => error instanceof ApiClientError && error.code === "identity_unverified");
    assert.equal(seen.length, 1);
    await act(async () => { next.resolve(accountB); await next.promise; });
    await client.request({ path: "/campaigns", method: "POST" });
    assert.equal(seen[1].get("X-Talky-Expected-Tenant"), "tenant-b");
});

test("a failed logout marker never submits an unbound logout for the next browser session", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    localStorage.setItem("talky.logout.pending", "1");
    let logouts = 0;
    api.logout = async () => { logouts++; };
    api.getMe = async () => ({ ...accountB });
    mount();
    await act(async () => { await Promise.resolve(); });
    assert.equal(logouts, 0);
});

test("a legacy optimistic login verifies its missing tenant without pinning it to no tenant", async () => {
    api.getMe = originalGetMe;
    const headers: Headers[] = [];
    globalThis.fetch = (async (_url, init) => { headers.push(new Headers(init?.headers)); return Response.json(accountB); }) as typeof fetch;
    mount();
    await act(async () => {
        auth.applyLoginResult({ user_id: accountB.id, email: accountB.email, role: accountB.role, access_token: "synthetic-b" });
    });
    await waitFor(() => assert.equal(auth.user?.tenant_id, "tenant-b"));
    assert.equal(headers[0].has("X-Talky-Expected-Tenant"), false);
    const client = createHttpClient({ baseUrl: "http://example.test" });
    await client.request({ path: "/campaigns", method: "POST" });
    assert.equal(headers.at(-1)?.get("X-Talky-Expected-Tenant"), "tenant-b");
});

test("refresh rotation updates the token owned by AuthProvider before retrying", async () => {
    await verifiedA();
    let refreshes = 0;
    const tokens: Array<string | null> = [];
    globalThis.fetch = (async (url, init) => {
        if (String(url).endsWith("/auth/refresh")) { refreshes++; return Response.json({ access_token: "rotated-a", user_id: "user-a", tenant_id: "tenant-a" }); }
        const token = new Headers(init?.headers).get("authorization"); tokens.push(token);
        return token === "Bearer rotated-a" ? Response.json({ saved: true }) : new Response(null, { status: 401 });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test" });
    await act(async () => { await client.request({ path: "/contacts" }); });
    assert.equal(auth.accessToken, "rotated-a");
    assert.equal(refreshes, 1);
    assert.deepEqual(tokens, ["Bearer synthetic-a", "Bearer rotated-a"]);
    assert.equal(auth.user?.tenant_id, "tenant-a");
});

test("callback token commitment reaches the live HTTP owner before its profile lookup", async () => {
    mount(); api.getMe = originalGetMe;
    const tokens: Array<string | null> = [];
    globalThis.fetch = (async (_url, init) => {
        tokens.push(new Headers(init?.headers).get("authorization"));
        return Response.json(accountB);
    }) as typeof fetch;
    await act(async () => { api.setToken("callback-b"); await api.getMe(); });
    assert.deepEqual(tokens, ["Bearer callback-b"]);
    assert.equal(auth.accessToken, "callback-b");
});

test("confirmed B scope loads and creates normally without importing legacy A history or destination", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-b");
    window.localStorage.setItem("talklee.notifications.v1", JSON.stringify([{ id: "old-a", type: "success", priority: "high", title: "Tenant A private lead", createdAt: Date.now() }]));
    window.localStorage.setItem("talklee.notifications.settings.v1", JSON.stringify({ ...defaultNotificationsSettings(), integrations: { webhook: { enabled: true, url: "https://tenant-a.example/webhook" } } }));
    api.getMe = async () => ({ ...accountB });
    mount();
    await screen.findByText("user-b");
    await waitFor(() => assert.equal(notificationsStore.getSnapshot().scopeKey, notificationScopeKey({ tenantId: "tenant-b", userId: "user-b" })));
    assert.equal(notificationsStore.getSnapshot().hydrated, true);
    act(() => { assert.ok(actions.create({ type: "success", title: "B own notification" })); });
    assert.ok(screen.getByText("B own notification"));
    assert.equal(screen.queryByText("Tenant A private lead") === null, true);
    assert.equal(notificationsStore.getSnapshot().settings.integrations.webhook.url, "");
});

test("logout clears notifications before server response and stale bootstrap cannot reactivate them", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-a");
    const oldProfile = deferred<typeof accountA>(); const logout = deferred<void>();
    api.getMe = () => oldProfile.promise; api.logout = () => logout.promise;
    mount();
    let completion!: ReturnType<typeof auth.logout>;
    act(() => { completion = auth.logout(); });
    assert.equal(auth.status, "anonymous");
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    await act(async () => { oldProfile.resolve(accountA); await oldProfile.promise; });
    assert.equal(auth.user, null);
    await act(async () => { logout.resolve(); await completion; });
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
});

test("late A profile refresh cannot overwrite a newly verified B login", async () => {
    await verifiedA();
    act(() => { actions.create({ type: "info", title: "A private history" }); });
    const capture = notificationsStore.capture(); const stale = deferred<typeof accountA>();
    api.getMe = () => stale.promise;
    let refresh!: Promise<void>;
    act(() => { refresh = auth.refreshUser({ silent: true }); });
    api.getMe = async () => ({ ...accountB });
    await act(async () => { auth.applyLoginResult({ user_id: accountB.id, email: accountB.email, role: accountB.role, access_token: "synthetic-b" }); });
    await waitFor(() => assert.equal(notificationsStore.getSnapshot().scopeKey, notificationScopeKey({ tenantId: "tenant-b", userId: "user-b" })));
    await act(async () => { stale.resolve(accountA); await refresh; });
    assert.equal(auth.user?.id, "user-b");
    assert.equal(capture.signal.aborted, true);
    assert.equal(capture.create({ type: "info", title: "Late A result" }), null);
    assert.equal(screen.queryByText("A private history") === null, true);
    act(() => { assert.ok(actions.create({ type: "info", title: "B current result" })); });
    assert.ok(screen.getByText("B current result"));
});

for (const silent of [true, false]) test(`same verified identity refresh (silent=${silent}) preserves notifications, settings and bound callback`, async () => {
    await verifiedA();
    act(() => { actions.create({ type: "info", title: "Retained A history" }); actions.setSettings({ historyRetentionDays: 12 }); });
    const before = notificationsStore.getSnapshot(); const bound = actions.create;
    await act(async () => { await auth.refreshUser({ silent }); });
    assert.equal(notificationsStore.getSnapshot().generation, before.generation);
    assert.equal(notificationsStore.getSnapshot().settings.historyRetentionDays, 12);
    assert.equal(actions.create, bound);
    assert.ok(screen.getByText("Retained A history"));
});

for (const latestSilent of [true, false]) test(`foreground refresh preserves scope and latest read (silent=${latestSilent}) settles loading and identity`, async () => {
    await verifiedA();
    act(() => { actions.create({ type: "info", title: "Foreground retained history" }); });
    const generation = notificationsStore.getSnapshot().generation;
    const first = deferred<typeof accountA>(); const latest = deferred<typeof accountB>();
    api.getMe = () => first.promise;
    let older!: Promise<void>; let newer!: Promise<void>;
    act(() => { older = auth.refreshUser(); });
    assert.equal(auth.status, "loading");
    assert.equal(notificationsStore.getSnapshot().generation, generation);
    assert.ok(screen.getByText("Foreground retained history"));
    api.getMe = () => latest.promise;
    act(() => { newer = auth.refreshUser({ silent: latestSilent }); });
    await act(async () => { latest.resolve(accountB); await newer; });
    await act(async () => { first.resolve(accountA); await older; });
    assert.equal(auth.user?.id, "user-b");
    assert.equal(auth.status, "authenticated");
    assert.equal(notificationsStore.getSnapshot().scopeKey, notificationScopeKey({ tenantId: "tenant-b", userId: "user-b" }));
});

test("cross-tab non-null token change masks A immediately and verifies B before enabling notifications", async () => {
    await verifiedA();
    act(() => { actions.create({ type: "info", title: "Private A" }); });
    const old = notificationsStore.capture(); const next = deferred<typeof accountB>();
    api.getMe = () => next.promise;
    act(() => { emitStorage("talklee.auth.token", "synthetic-b"); });
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    assert.equal(old.signal.aborted, true);
    assert.equal(screen.queryByText("Private A") === null, true);
    await act(async () => { next.resolve(accountB); await next.promise; });
    await waitFor(() => assert.equal(auth.user?.id, "user-b"));
    assert.equal(notificationsStore.getSnapshot().scopeKey, notificationScopeKey({ tenantId: "tenant-b", userId: "user-b" }));
    act(() => { assert.ok(actions.create({ type: "info", title: "Current B" })); });
});

test("unverified same-account token rotation aborts old work but restores saved history after verification", async () => {
    await verifiedA();
    act(() => { actions.create({ type: "info", title: "Saved A" }); });
    const old = notificationsStore.capture(); const generation = old.generation;
    await act(async () => { emitStorage("talklee.auth.token", "synthetic-a-rotated"); });
    assert.equal(old.signal.aborted, true);
    assert.ok(notificationsStore.getSnapshot().generation > generation);
    assert.ok(screen.getByText("Saved A"));
    act(() => { assert.ok(actions.create({ type: "info", title: "New callback after rotation" })); });
});

test("cookie-only logout broadcasts an identity boundary even with no stored bearer token", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    api.getMe = async () => ({ ...accountA }); const logout = deferred<void>(); api.logout = () => logout.promise;
    mount(); await screen.findByText("user-a");
    act(() => { actions.create({ type: "info", title: "Cookie-only A" }); });
    const old = notificationsStore.capture(); let completion!: ReturnType<typeof auth.logout>;
    act(() => { completion = auth.logout(); });
    assert.equal(window.localStorage.getItem("talklee.auth.token"), null);
    assert.equal(JSON.parse(window.localStorage.getItem(AUTH_IDENTITY_EVENT_KEY)!).kind, "logout");
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    assert.equal(old.signal.aborted, true);
    assert.equal(screen.queryByText("Cookie-only A") === null, true);
    await act(async () => { logout.resolve(); await completion; });
});

test("cross-tab cookie logout fences an outstanding profile response without a bearer-key event", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    api.getMe = async () => ({ ...accountA }); mount(); await screen.findByText("user-a");
    const old = deferred<typeof accountA>(); api.getMe = () => old.promise;
    let read!: Promise<void>;
    act(() => { read = auth.refreshUser({ silent: true }); emitStorage(AUTH_IDENTITY_EVENT_KEY, JSON.stringify({ kind: "logout", nonce: "synthetic" })); });
    assert.equal(auth.user, null);
    await act(async () => { old.resolve(accountA); await read; });
    assert.equal(auth.user, null);
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
});

test("a verified user without tenant identity remains unscoped and cannot create notifications", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-platform-user");
    let calls = 0;
    api.getMe = async () => { calls++; return { ...accountA, id: "platform-user", tenant_id: undefined, email: "platform@example.test", role: "platform_admin", minutes_remaining: 0 }; };
    mount(); await screen.findByText("platform-user");
    await waitFor(() => assert.equal(calls, 2));
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    assert.equal(actions.create({ type: "info", title: "No invented tenant" }), null);
});

test("logout then login as A creates a new generation that rejects pre-logout A work", async () => {
    await verifiedA(); const old = notificationsStore.capture(); api.logout = async () => {};
    await act(async () => { await auth.logout(); });
    await act(async () => { auth.applyLoginResult({ user_id: accountA.id, email: accountA.email, role: accountA.role, access_token: "synthetic-a-again" }); });
    await waitFor(() => assert.equal(notificationsStore.getSnapshot().scopeKey, old.scopeKey));
    assert.equal(old.create({ type: "info", title: "Old session result" }), null);
    act(() => { assert.ok(actions.create({ type: "info", title: "New A session result" })); });
});

test("a profile refresh started by an old timer after logout begins cannot restore the logged-out account", async () => {
    await verifiedA(); const logout = deferred<void>(); api.logout = () => logout.promise;
    const oldRefresh = auth.refreshUser; let completion!: ReturnType<typeof auth.logout>;
    act(() => { completion = auth.logout(); });
    await act(async () => { await oldRefresh({ silent: true }); });
    assert.equal(auth.user, null);
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    await act(async () => { logout.resolve(); await completion; });
});

test("blocked storage still receives cookie logout over BroadcastChannel and fences delayed profile reads", async () => {
    const { Channel, channels } = installBroadcastChannel(); blockStorage();
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    api.getMe = async () => ({ ...accountA }); const view = mount(); await screen.findByText("user-a");
    assert.equal(auth.notificationSyncAvailable, true);
    act(() => { assert.ok(actions.create({ type: "info", title: "Memory-only private A" })); });
    const capture = notificationsStore.capture(); const stale = deferred<typeof accountA>(); api.getMe = () => stale.promise;
    let refresh!: Promise<void>; const otherTab = new Channel(AUTH_IDENTITY_EVENT_KEY);
    await act(async () => { refresh = auth.refreshUser({ silent: true }); otherTab.postMessage({ kind: "logout", nonce: "blocked-logout" }); });
    assert.equal(auth.user, null);
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    assert.equal(capture.signal.aborted, true);
    await act(async () => { stale.resolve(accountA); await refresh; });
    assert.equal(auth.user, null);
    view.unmount(); otherTab.close(); assert.equal(channels.size, 0);
});

test("blocked-storage account change uses verified B scope and duplicate channel/storage marker verifies only once", async () => {
    const { Channel } = installBroadcastChannel(); blockStorage(); process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    api.getMe = async () => ({ ...accountA }); mount(); await screen.findByText("user-a");
    act(() => { actions.create({ type: "info", title: "Old private A" }); });
    const stale = notificationsStore.capture(); const next = deferred<typeof accountB>(); let reads = 0;
    api.getMe = () => { reads++; return next.promise; };
    const otherTab = new Channel(AUTH_IDENTITY_EVENT_KEY); const marker = { kind: "changed", nonce: "one-session-marker" };
    await act(async () => { otherTab.postMessage(marker); });
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    assert.equal(stale.signal.aborted, true);
    assert.equal(screen.queryByText("Old private A") === null, true);
    await act(async () => { otherTab.postMessage(marker); window.dispatchEvent(new window.StorageEvent("storage", { key: AUTH_IDENTITY_EVENT_KEY, newValue: JSON.stringify(marker) })); });
    assert.equal(reads, 1);
    await act(async () => { next.resolve(accountB); await next.promise; });
    assert.equal(auth.user?.id, "user-b");
    assert.equal(notificationsStore.getSnapshot().scopeKey, notificationScopeKey({ tenantId: "tenant-b", userId: "user-b" }));
    act(() => { assert.ok(actions.create({ type: "info", title: "New B only" })); });
    assert.equal(stale.create({ type: "info", title: "Delayed A result" }), null);
    otherTab.close();
});

test("local cookie logout and login publish secret-free BroadcastChannel markers when storage writes fail", async () => {
    const { Channel } = installBroadcastChannel(); blockStorage(); process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    const peer = new Channel(AUTH_IDENTITY_EVENT_KEY); const received: unknown[] = []; peer.onmessage = event => received.push(event.data);
    api.getMe = async () => ({ ...accountA }); api.logout = async () => {}; mount(); await screen.findByText("user-a");
    await act(async () => { await auth.logout(); });
    api.getMe = async () => ({ ...accountB });
    await act(async () => { auth.applyLoginResult({ user_id: accountB.id, email: accountB.email, role: accountB.role }); });
    await waitFor(() => assert.equal(auth.user?.tenant_id, "tenant-b"));
    assert.deepEqual(received.map(value => (value as { kind: string }).kind), ["logout", "changed"]);
    for (const value of received) assert.deepEqual(Object.keys(value as object).sort(), ["kind", "nonce"]);
    peer.close();
});

test("notifications remain unscoped when neither storage nor BroadcastChannel can synchronize cookie identity", async () => {
    Object.defineProperty(window, "BroadcastChannel", { configurable: true, writable: true, value: undefined }); blockStorage();
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false"; api.getMe = async () => ({ ...accountA });
    mount(); await screen.findByText("user-a");
    assert.equal(auth.status, "authenticated");
    assert.equal(auth.notificationSyncAvailable, false);
    assert.equal(notificationsStore.getSnapshot().scopeKey, null);
    assert.equal(actions.create({ type: "info", title: "No unsafe polling scope" }), null);
});
