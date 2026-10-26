import React from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AdminOperationsConsole } from "@/components/admin/admin-operations-console";
import { SuspensionStateProvider, useSuspensionState } from "@/components/admin/suspension-state-provider";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api, MeResponseSchema } from "@/lib/api";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { NotificationsIdentityProvider } from "@/lib/notifications-client";

// Actual React console, AuthProvider, QueryClient, hooks, decoder and HTTP
// request ownership; canonical profile/HTTP responses are synthetic. This
// component fixture does not claim native RouteGuard or database acceptance.
const ADMIN = "41000000-0000-4000-8000-000000000001";
const NEXT_ADMIN = "41000000-0000-4000-8000-000000000002";
const TENANT = "51000000-0000-4000-8000-000000000001";
const NEXT_TENANT = "51000000-0000-4000-8000-000000000002";
const AUDIT = "31000000-0000-4000-8000-000000000001";
const BEFORE = "2026-10-21T12:00:00.000001+00:00";
const AFTER = "2026-10-21T12:01:00.000002+00:00";
const DIRECTORY = "/api/v1/admin/tenants";
const originalFetch = globalThis.fetch, originalGetMe = api.getMe;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const tenant = (extra: Record<string, unknown> = {}) => ({ id: TENANT, business_name: "Owned A tenant", status: "active", updated_at: BEFORE, ...extra });
type Request = { url: URL; method: string; body?: Record<string, unknown> };
let calls: Request[], rows: ReturnType<typeof tenant>[], profiles: number;
let profile: ReturnType<typeof MeResponseSchema.parse>;
let override: ((request: Request) => Response | Promise<Response> | undefined) | undefined;
let client: QueryClient;
const reads = () => calls.filter(call => call.url.pathname === DIRECTORY && call.method === "GET");
const writes = () => calls.filter(call => call.method === "POST");
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const receipt = (status: "active" | "suspended", extra: Record<string, unknown> = {}) => ({ id: TENANT, status, updated_at: AFTER, changed: true, audit_event_id: AUDIT, ...extra });

beforeEach(() => {
    window.localStorage.clear(); window.sessionStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
    calls = []; rows = [tenant()]; profiles = 0; override = undefined;
    profile = MeResponseSchema.parse({ id: ADMIN, email: "tenant-operator@example.test", role: "platform_admin", tenant_id: TENANT,
        minutes_remaining: 0, minutes_state: "known" });
    api.getMe = async () => { profiles += 1; return profile; };
    globalThis.fetch = (async (input, init) => {
        const request: Request = { url: new URL(String(input), "http://localhost"), method: init?.method ?? "GET",
            body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined };
        calls.push(request);
        const result = override?.(request); if (result) return result;
        if (request.method !== "GET") throw new Error(`Unexpected synthetic write ${request.url.pathname}`);
        if (request.url.pathname === DIRECTORY) return json(rows);
        if (request.url.pathname === "/api/v1/admin/partners") return json({ items: [], total: 0, page: 1, page_size: 8 });
        if (["/api/v1/admin/audit/logs", "/api/v1/admin/security-events/events"].includes(request.url.pathname)) return json([]);
        return json({ detail: "Unexpected synthetic route" }, 404);
    }) as typeof fetch;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } } });
});
afterEach(() => {
    cleanup(); client.clear(); globalThis.fetch = originalFetch; api.getMe = originalGetMe;
    notificationsStore.setIdentity(null); window.localStorage.clear(); window.sessionStorage.clear(); clearFreshLoginGrace(); invalidateRequestIdentity();
});

function IdentityControls() {
    const auth = useAuth(), suspension = useSuspensionState();
    return <>
        <button onClick={() => auth.applyLoginResult({ user_id: NEXT_ADMIN, email: "next-operator@example.test", role: "platform_admin", tenant_id: NEXT_TENANT })}>Replace tenant fixture identity</button>
        <button onClick={() => void auth.refreshUser()}>Recheck tenant fixture identity</button>
        <button onClick={() => {
            profile = MeResponseSchema.parse({ ...profile, role: "partner_admin" });
            void auth.refreshUser({ silent: true });
        }}>Demote tenant fixture operator</button>
        <button onClick={() => invalidateRequestIdentity()}>Retire tenant fixture generation</button>
        <output aria-label="Fixture operator role">{auth.user?.role ?? "none"}</output>
        <output aria-label="Fixture canonical tenant account state">{suspension.state.tenantStatus}</output>
    </>;
}
async function draw() {
    render(<QueryClientProvider client={client}><AuthProvider><NotificationsIdentityProvider /><SuspensionStateProvider>
        <IdentityControls /><AdminOperationsConsole />
    </SuspensionStateProvider></AuthProvider></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("tab", { name: "Suspensions" }));
    await waitFor(() => assert.equal(reads().length > 0, true));
}
async function row(name = "Owned A tenant") {
    const label = await screen.findByText(name, { exact: true });
    const container = label.closest(".border"); assert.equal(Boolean(container), true);
    return within(container as HTMLElement);
}
async function openReview(name = "Owned A tenant", restore = false) {
    const target = await row(name);
    // Use the existing label only to reach an independent owner/receipt
    // baseline; the final wording has its own explicit semantic assertions.
    const button = target.queryByRole("button", { name: restore ? "Resume subscription" : "Suspend subscription" })
        ?? target.getByRole("button", { name: restore ? "Reactivate tenant" : "Suspend tenant" });
    fireEvent.click(button); return screen.getByRole("dialog");
}
const confirmButton = () => within(screen.getByRole("dialog")).getByRole("button", { name: /^Confirm / });
function reason(value = "Reviewed subscription decision") { fireEvent.change(screen.getByRole("textbox", { name: /Reason/ }), { target: { value } }); }
function retainedClick(button: HTMLElement) {
    const key = Object.keys(button).find(value => value.startsWith("__reactProps")); assert.equal(Boolean(key), true);
    const props = (button as unknown as Record<string, unknown>)[key!] as { onClick: (event: { currentTarget: HTMLElement; preventDefault(): void }) => void };
    return () => props.onClick({ currentTarget: button, preventDefault() {} });
}

test("tenant directory same QueryClient cannot publish A rows before replacement B read or after B403", async () => {
    await draw(); await row(); const pending = deferred<Response>();
    override = request => request.url.pathname === DIRECTORY ? pending.promise : undefined;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Replace tenant fixture identity" }));
        assert.equal(Boolean(screen.queryByText("Owned A tenant", { exact: true })), false);
        assert.equal(Boolean(screen.queryByRole("button", { name: /^(Suspend tenant|Suspend subscription)$/ })), false);
        await act(async () => pending.resolve(json({ detail: "Replacement tenant directory denied" }, 403)));
        await screen.findByText("Tenant controls unavailable");
        assert.equal(Boolean(screen.queryByText("No tenants found")), false);
    } finally { await act(async () => pending.resolve(json({ detail: "Replacement tenant directory denied" }, 403))); }
});

test("tenant filter retires old rows and retained row actions while its actual response is pending", async () => {
    await draw(); const target = await row();
    const oldButton = target.queryByRole("button", { name: "Suspend subscription" }) ?? target.getByRole("button", { name: "Suspend tenant" });
    const oldClick = retainedClick(oldButton), pending = deferred<Response>();
    override = request => request.url.pathname === DIRECTORY && request.url.searchParams.has("search") ? pending.promise : undefined;
    try {
        fireEvent.change(screen.getByPlaceholderText("Tenant business name"), { target: { value: "another tenant" } });
        const staleRowVisible = Boolean(screen.queryByText("Owned A tenant", { exact: true }));
        // Retained React handler is a component-level ownership probe, not a
        // claim that a native click can activate an unmounted/disabled button.
        await act(async () => oldClick());
        assert.deepEqual({ staleRowVisible, oldReviewVisible: Boolean(screen.queryByRole("dialog")), posts: writes().length },
            { staleRowVisible: false, oldReviewVisible: false, posts: 0 });
        assert.equal(Boolean(screen.queryByText("No tenants found")), false);
    } finally { await act(async () => pending.resolve(json([]))); }
});

test("tenant pending canonical profile masks cached rows without asserting an empty result", async () => {
    await draw(); await row(); const pending = deferred<ReturnType<typeof MeResponseSchema.parse>>();
    api.getMe = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Recheck tenant fixture identity" }));
        assert.equal(Boolean(screen.queryByText("Owned A tenant", { exact: true })), false);
        assert.equal(Boolean(screen.queryByText("Loading tenants")), true);
        assert.equal(Boolean(screen.queryByText("No tenants found")), false);
        assert.equal(Boolean(screen.queryByRole("button", { name: /^(Suspend tenant|Suspend subscription)$/ })), false);
    } finally { await act(async () => pending.resolve(profile)); }
});

test("tenant retained Retry cannot bypass unconfirmed canonical identity", async () => {
    override = request => request.url.pathname === DIRECTORY ? json({ detail: "Synthetic directory unavailable" }, 503) : undefined;
    await draw(); await screen.findByText("Tenant controls unavailable");
    const retry = retainedClick(screen.getByRole("button", { name: "Retry" }));
    const pending = deferred<ReturnType<typeof MeResponseSchema.parse>>(); api.getMe = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Recheck tenant fixture identity" }));
        const before = reads().length; await act(async () => retry());
        assert.equal(reads().length, before, "Manual refetch must not bypass the same readiness boundary");
        assert.equal(Boolean(screen.queryByText("Loading tenants")), true);
        assert.equal(Boolean(screen.queryByText("No tenants found")), false);
    } finally { await act(async () => pending.resolve(profile)); }
});

for (const retired of ["identity", "role", "generation"] as const) test(`tenant open confirmation and retained submit retire on ${retired} change`, async () => {
    override = request => request.method === "POST" ? json(receipt("suspended")) : undefined;
    await draw(); await openReview(); reason(); const confirm = retainedClick(confirmButton());
    if (retired === "identity") fireEvent.click(screen.getByRole("button", { name: "Replace tenant fixture identity", hidden: true }));
    else if (retired === "role") {
        fireEvent.click(screen.getByRole("button", { name: "Demote tenant fixture operator", hidden: true }));
        await waitFor(() => assert.equal(screen.getByLabelText("Fixture operator role").textContent, "partner_admin"));
    } else fireEvent.click(screen.getByRole("button", { name: "Retire tenant fixture generation", hidden: true }));
    const oldReviewVisible = Boolean(screen.queryByRole("dialog"));
    await act(async () => confirm());
    assert.deepEqual({ oldReviewVisible, posts: writes().length }, { oldReviewVisible: false, posts: 0 });
});

test("tenant late A completion cannot close or contaminate B review or refresh B authority", async () => {
    const pending = deferred<Response>(); override = request => request.method === "POST" ? pending.promise : undefined;
    await draw(); await openReview(); reason(); fireEvent.click(confirmButton());
    await waitFor(() => assert.equal(writes().length, 1));
    try {
        profile = MeResponseSchema.parse({ ...profile, id: NEXT_ADMIN, tenant_id: NEXT_TENANT });
        rows = [tenant({ id: NEXT_TENANT, business_name: "Owned B tenant" })];
        fireEvent.click(screen.getByRole("button", { name: "Replace tenant fixture identity", hidden: true }));
        // Explicit current-owner read isolates completion ownership from the
        // independently tested shared-cache defect; response/decoder remain real.
        await act(async () => { await client.invalidateQueries({ queryKey: ["adminTenants"] }); });
        await openReview("Owned B tenant"); reason("B has a different reviewed decision");
        assert.match(screen.getByRole("dialog").textContent ?? "", /Owned B tenant/, "Precondition: B owns the visible review before A completes");
        assert.equal((confirmButton() as HTMLButtonElement).disabled, false, "Precondition: B has an enabled independent confirmation before A completes");
        const before = profiles; await act(async () => pending.resolve(json(receipt("suspended"))));
        assert.equal(Boolean(screen.queryByRole("dialog")), true);
        assert.match(screen.getByRole("dialog").textContent ?? "", /Owned B tenant/);
        assert.equal((screen.getByRole("textbox", { name: /Reason/ }) as HTMLInputElement).value, "B has a different reviewed decision");
        assert.equal((confirmButton() as HTMLButtonElement).disabled, false);
        assert.equal(within(screen.getByRole("dialog")).queryAllByRole("alert").filter(node => /account changed|identity|unconfirmed|failed/i.test(node.textContent ?? "")).length, 0);
        assert.equal(profiles, before); assert.equal(writes().length, 1);
    } finally { await act(async () => pending.resolve(json(receipt("suspended")))); }
});

test("tenant late completion after same-user silent demotion cannot publish into retired authority caches or refresh authority", async () => {
    const pending = deferred<Response>(); override = request => request.method === "POST" ? pending.promise : undefined;
    await draw(); await openReview(); reason(); fireEvent.click(confirmButton());
    await waitFor(() => assert.equal(writes().length, 1));
    const originalQuery = client.getQueryCache().findAll({ queryKey: ["adminTenants"] })
        .find(query => query.getObserversCount() > 0 && query.state.status === "success");
    assert.equal(Boolean(originalQuery), true, "Precondition: the admitted operator has a populated tenant cache");
    try {
        fireEvent.click(screen.getByRole("button", { name: "Demote tenant fixture operator", hidden: true }));
        await waitFor(() => assert.equal(screen.getByLabelText("Fixture operator role").textContent, "partner_admin"));
        const beforeProfiles = profiles, beforeReads = reads().length;
        await act(async () => pending.resolve(json(receipt("suspended"))));
        await waitFor(() => assert.equal(client.getMutationCache().getAll().some(mutation => mutation.state.status === "pending"), false));
        const cached = client.getQueryData<{ items: Array<{ id: string; status: string }> }>(originalQuery!.queryKey);
        assert.equal(cached?.items.find(item => item.id === TENANT)?.status, "active", "A late result cannot rewrite the previous authority's cached directory");
        assert.equal(Boolean(screen.queryByRole("dialog")), false);
        assert.equal(profiles, beforeProfiles); assert.equal(reads().length, beforeReads); assert.equal(writes().length, 1);
        // The POST was already admitted before demotion. This probes only late
        // client publication/continuation, not cancellation of a backend write.
    } finally { await act(async () => pending.resolve(json(receipt("suspended")))); }
});

for (const restore of [false, true]) test(`tenant valid reviewed ${restore ? "resume" : "suspend"} sends one exact versioned write`, async () => {
    rows = [tenant({ status: restore ? "suspended" : "active" })];
    override = request => {
        if (request.method !== "POST") return undefined;
        rows = [tenant({ status: restore ? "active" : "suspended", updated_at: AFTER })];
        return json(receipt(restore ? "active" : "suspended"));
    };
    await draw(); await openReview("Owned A tenant", restore); reason(); fireEvent.click(confirmButton());
    await waitFor(() => assert.equal(Boolean(screen.queryByRole("dialog")), false));
    assert.equal(writes().length, 1);
    assert.equal(writes()[0]!.url.pathname, `${DIRECTORY}/${TENANT}/${restore ? "resume" : "suspend"}`);
    assert.deepEqual(writes()[0]!.body, { expected_updated_at: BEFORE, reason: "Reviewed subscription decision" });
    await waitFor(() => assert.equal(reads().length > 1, true, "The real identity bridge permits the settled current-owner refresh"));
    await waitFor(() => assert.equal(client.isFetching({ queryKey: ["adminTenants"] }), 0));
    const current = await row();
    const nextAction = current.queryByRole("button", { name: restore ? "Suspend subscription" : "Resume subscription" })
        ?? current.getByRole("button", { name: restore ? "Suspend tenant" : "Reactivate tenant" });
    assert.equal((nextAction as HTMLButtonElement).disabled, false, "The next reviewed action must be available once the previous write and reread settle");
});

test("tenant stale reviewed version shows conflict without automatic replay or status replacement", async () => {
    override = request => request.method === "POST" ? json({ detail: { code: "tenant_changed", message: "Tenant changed since review" } }, 409) : undefined;
    await draw(); await openReview(); reason(); fireEvent.click(confirmButton());
    await screen.findByText(/Tenant changed since review/);
    assert.equal(Boolean(screen.queryByRole("dialog")), true); assert.equal(writes().length, 1);
    const current = await row(); assert.equal(Boolean(current.queryByText(/^(Subscription: )?active$/i)), true);
});

test("confirmed subscription change refreshes a previously cached filter when it is revisited", async () => {
    await draw(); await row();
    const search = screen.getByPlaceholderText("Tenant business name");
    fireEvent.change(search, { target: { value: "Owned" } });
    await waitFor(() => assert.equal(reads().filter(request => request.url.searchParams.get("search") === "Owned").length, 1));
    await row(); fireEvent.change(search, { target: { value: "" } });
    await waitFor(() => assert.equal(client.isFetching({ queryKey: ["adminTenants"] }), 0));
    override = request => {
        if (request.method !== "POST") return undefined;
        rows = [tenant({ status: "suspended", updated_at: AFTER })];
        return json(receipt("suspended"));
    };
    await openReview(); reason(); fireEvent.click(confirmButton());
    await waitFor(() => assert.equal(Boolean(screen.queryByRole("dialog")), false));
    await waitFor(() => assert.equal(client.isFetching({ queryKey: ["adminTenants"] }), 0));
    fireEvent.change(search, { target: { value: "Owned" } });
    await waitFor(() => assert.equal(reads().filter(request => request.url.searchParams.get("search") === "Owned").length, 2));
    await waitFor(() => assert.equal(client.isFetching({ queryKey: ["adminTenants"] }), 0));
    assert.equal(Boolean((await row()).queryByText("Subscription: suspended", { exact: true })), true);
    assert.equal(writes().length, 1);
});

test("tenantless platform admin refreshes saved subscription without a notification scope", async () => {
    profile = MeResponseSchema.parse({ ...profile, tenant_id: null });
    override = request => {
        if (request.method !== "POST") return undefined;
        rows = [tenant({ status: "suspended", updated_at: AFTER })];
        return json(receipt("suspended"));
    };
    await draw(); await openReview();
    assert.equal(notificationsStore.getSnapshot().scopeKey, null, "Verified platform admin has no tenant notification scope");
    reason(); fireEvent.click(confirmButton());
    await waitFor(() => assert.equal(Boolean(screen.queryByRole("dialog")), false));
    await waitFor(() => assert.ok(reads().length > 1, "Business completion rereads the directory independently of optional notifications"));
    await waitFor(() => assert.equal(client.isFetching({ queryKey: ["adminTenants"] }), 0));
    const current = await row();
    assert.equal(Boolean(current.queryByText("Subscription: suspended", { exact: true })), true);
    assert.equal((current.getByRole("button", { name: "Resume subscription" }) as HTMLButtonElement).disabled, false);
    assert.equal(writes().length, 1); assert.equal(notificationsStore.getSnapshot().scopeKey, null);
});

test("tenant malformed changed receipt cannot close review as a confirmed success", async () => {
    override = request => request.method === "POST" ? json(receipt("suspended", { audit_event_id: null })) : undefined;
    await draw(); await openReview(); reason(); fireEvent.click(confirmButton());
    await screen.findByText(/durable audit receipt/);
    assert.equal(Boolean(screen.queryByRole("dialog")), true); assert.equal(writes().length, 1);
    assert.equal(Boolean((await row()).queryByText(/^(Subscription: )?active$/i)), true);
});

test("tenant unavailable mutation result remains unconfirmed and is never automatically replayed", async () => {
    override = request => request.method === "POST" ? json({ detail: "Synthetic subscription outcome unavailable" }, 503) : undefined;
    await draw(); await openReview(); reason(); fireEvent.click(confirmButton());
    await screen.findByText(/Synthetic subscription outcome unavailable/);
    assert.equal(Boolean(screen.queryByRole("dialog")), true); assert.equal(writes().length, 1);
    assert.equal(Boolean((await row()).queryByText(/^(Subscription: )?active$/i)), true);
});

test("tenant active subscription is labelled separately from a held canonical tenant account", async () => {
    profile = MeResponseSchema.parse({ ...profile, tenant_status: "suspended" });
    await draw(); const current = await row();
    assert.equal(screen.getByLabelText("Fixture canonical tenant account state").textContent, "suspended");
    assert.equal(Boolean(screen.queryByText("Tenant subscription controls", { exact: true })), true);
    assert.equal(Boolean(current.queryByText("Subscription: active", { exact: true })), true);
    assert.equal(Boolean(current.queryByRole("button", { name: "Suspend subscription" })), true);
    assert.equal(Boolean(screen.queryByRole("combobox", { name: "Tenant subscription status" })), true);
    assert.match(document.body.textContent ?? "", /formal (holds|restrictions).*separate|separate.*formal (holds|restrictions)/i);
    assert.doesNotMatch(document.body.textContent ?? "", /Apply tenant-level blocks instantly|immediately blocks tenant-scoped activity/);
    // The canonical held-state fixture is not a new enforcement test. The
    // existing native/database evidence establishes that this combination is
    // possible; this case verifies only the operator-facing distinction.
    assert.equal(writes().length, 0);
});
