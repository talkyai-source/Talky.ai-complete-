import React from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AdminOperationsConsole } from "@/components/admin/admin-operations-console";
import { SuspensionStateProvider, useSuspensionState } from "@/components/admin/suspension-state-provider";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api, MeResponseSchema } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";

const originalFetch = globalThis.fetch, originalGetMe = api.getMe;
const AT = "2026-10-21T12:34:56+00:00";
const ADMIN = "40000000-0000-4000-8000-000000000001", OTHER_ADMIN = "40000000-0000-4000-8000-000000000002";
const TENANT = "50000000-0000-4000-8000-000000000001", HOLD = "20000000-0000-4000-8000-000000000001", AUDIT = "30000000-0000-4000-8000-000000000001";
const partner = (state: string, index = 1) => ({ id: `10000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    name: `Owned ${state} partner`, status: state, tenant_count: 7, suspended_at: state === "suspended" ? AT : null, updated_at: AT });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
type Request = { url: URL; method: string; body?: Record<string, unknown> };
let rows: ReturnType<typeof partner>[];
let calls: Request[], meReads: number, total: number | undefined;
let profile: ReturnType<typeof MeResponseSchema.parse>, replacementRole: "platform_admin" | "partner_admin";
let override: ((request: Request) => Response | Promise<Response> | undefined) | undefined;
let holdEvents: ReturnType<typeof holdEvent>[];
let qc: QueryClient;
const holdEvent = (extra: Record<string, unknown> = {}) => ({ suspension_id: HOLD, target_type: "partner", target_id: partner("active").id,
    suspension_type: "ADMIN", reason_category: "administrative_review", reason_description: "Reviewed partner change", suspended_at: AT,
    suspended_until: null, is_active: true, appeal_reason: null, appeal_decision: null, appeal_response: null, audit_log_id: AUDIT,
    restored_at: null, restore_reason: null, suspended_by: ADMIN, evidence: null, ...extra });
const saved = (restored = false) => ({ suspension_id: HOLD, audit_event_id: AUDIT, target_type: "partner", target_id: partner("active").id,
    status: restored ? "restored" : "suspended", ...(restored ? { restored: true, effective_status: "restricted", remaining_hold_ids: [] } : {}) });
const status = (id: string) => ({ target_type: "partner", target_id: id, status: "active", is_suspended: holdEvents.some(event => event.is_active),
    active_suspension: holdEvents.find(event => event.is_active) ?? null, suspension_history: holdEvents,
    effective_status: holdEvents.some(event => event.is_active) ? "suspended" : "active",
    remaining_hold_ids: holdEvents.filter(event => event.is_active).map(event => event.suspension_id), history_complete: true, history_limit: 200 });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const directoryCalls = () => calls.filter(call => call.url.pathname === "/api/v1/admin/partners");
const writes = () => calls.filter(call => call.method === "POST");
beforeEach(() => {
    window.localStorage.clear(); window.sessionStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
    rows = []; calls = []; holdEvents = []; meReads = 0; total = undefined; override = undefined; replacementRole = "platform_admin";
    // Authentication is a canonical profile fixture; the console, hooks, request
    // client and partner schema below are real, with synthetic HTTP transport.
    profile = MeResponseSchema.parse({ id: ADMIN, email: "operator@example.test", role: "platform_admin",
        tenant_id: TENANT, minutes_remaining: 0, minutes_state: "known" });
    api.getMe = async () => { meReads += 1; return profile; };
    globalThis.fetch = (async (input, init) => {
        const url = new URL(String(input), "http://localhost"), method = init?.method ?? "GET";
        const request = { url, method, body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : undefined };
        calls.push(request);
        const custom = override?.(request); if (custom) return custom;
        if (method !== "GET") throw new Error(`Unexpected mutation ${url.pathname}`);
        if (url.pathname === "/api/v1/admin/partners") return json({ items: rows, total: total ?? rows.length, page: Number(url.searchParams.get("page") ?? 1), page_size: 8 });
        if (/\/admin\/suspensions\/partners\/[^/]+\/status$/.test(url.pathname)) return json(status(url.pathname.split("/").at(-2)!));
        // Evidence is independently unavailable in this fixture. A valid saved
        // decision must not be reclassified by this unrelated read failure.
        if (url.pathname.includes("/suspensions/propagation-status/")) return json({ detail: "Synthetic evidence unavailable" }, 503);
        if (url.pathname === "/api/v1/admin/tenants") return json([]);
        if (["/api/v1/admin/audit/logs", "/api/v1/admin/security-events/events"].includes(url.pathname)) return json([]);
        throw new Error(`Unexpected fixture path ${url.pathname}`);
    }) as typeof fetch;
    qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 30_000, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } } });
});
afterEach(() => {
    cleanup(); qc.clear(); api.getMe = originalGetMe; globalThis.fetch = originalFetch;
    notificationsStore.setIdentity(null); window.localStorage.clear(); clearFreshLoginGrace(); invalidateRequestIdentity();
});
function IdentityControl() {
    const auth = useAuth(), suspension = useSuspensionState();
    return <><button onClick={() => auth.applyLoginResult({ user_id: OTHER_ADMIN, email: "replacement@example.test", role: replacementRole, tenant_id: TENANT })}>Replace fixture identity</button>
        <button onClick={() => void auth.refreshUser()}>Recheck fixture identity</button>
        <output aria-label="Canonical profile loading">{String(auth.loading)}</output>
        <output aria-label="Canonical partner restriction">{suspension.state.partnerStatus}</output></>;
}
async function draw() {
    const view = render(<QueryClientProvider client={qc}><AuthProvider><SuspensionStateProvider>
        <IdentityControl /><AdminOperationsConsole />
    </SuspensionStateProvider></AuthProvider></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("tab", { name: "Suspensions" }));
    await waitFor(() => assert.equal(calls.some(call => call.url.pathname === "/api/v1/admin/partners"), true));
    return view;
}
async function rowFor(state: string) {
    await waitFor(() => assert.equal(Boolean(screen.queryByText(`Owned ${state} partner`, { exact: true })), true));
    const node = screen.getByText(`Owned ${state} partner`, { exact: true }).closest(".border");
    assert.equal(Boolean(node), true);
    return within(node as HTMLElement);
}

test("the real console retains usable rows when a page includes pending deletion and unknown", async () => {
    rows = [partner("active"), partner("suspended", 2), partner("pending_deletion", 3), partner("unknown", 4)];
    await draw();
    for (const state of ["active", "suspended", "pending_deletion", "unknown"]) {
        const row = await rowFor(state);
        assert.equal(Boolean(row.getByText(state.replaceAll("_", " "), { exact: true })), true);
    }
    assert.equal(Boolean(screen.queryByText("Partner controls unavailable")), false);
});

for (const state of ["active", "suspended"] as const) test(`${state} partner review and actual metadata remain visible`, async () => {
    rows = [partner(state)]; await draw(); const row = await rowFor(state);
    assert.equal((row.getByRole("button", { name: "Manage suspension" }) as HTMLButtonElement).disabled, false);
    assert.equal(Boolean(row.getByText(/7 tenants/)), true);
    assert.equal(Boolean(row.getByText(/Updated .*2026/)), true);
    assert.equal(row.queryAllByRole("button", { name: /^(Suspend|Reactivate) partner$/ }).length, 0);
});

for (const state of ["pending_deletion", "unknown"] as const) test(`${state} does not enable a guessed legacy partner state change`, async () => {
    rows = [partner(state)]; await draw(); const row = await rowFor(state);
    const legacy = row.getAllByRole("button").filter(button => /^(Suspend|Reactivate) partner$/.test(button.textContent?.trim() ?? ""));
    assert.equal(legacy.length, 0);
    assert.equal(Boolean(screen.queryByRole("dialog")), false);
    assert.equal(calls.some(call => call.method !== "GET"), false);
    assert.equal((row.getByRole("button", { name: "Manage suspension" }) as HTMLButtonElement).disabled, false);
});

for (const [value, label] of [["pending_deletion", "Pending deletion"], ["unknown", "Unknown"]]) test(`partner filter exposes ${label} without substituting active`, async () => {
    rows = [partner("active")]; await draw(); await rowFor("active");
    fireEvent.click(screen.getByRole("combobox", { name: "Partner status" }));
    const option = screen.queryByRole("option", { name: label });
    assert.equal(Boolean(option), true);
    fireEvent.click(option!);
    await waitFor(() => assert.equal(calls.some(call => call.url.pathname === "/api/v1/admin/partners" && call.url.searchParams.get("status") === value), true));
});

// Candidate acceptance exercises the existing formal flow, not a replacement
// mutation mock. The old entry fallback is used only in integration baselines
// so missing parent refresh is measured independently of the label defect.
async function openFormal(state = "active") {
    const row = await rowFor(state);
    fireEvent.click(row.queryByRole("button", { name: "Manage suspension" }) ?? row.getByRole("button", { name: "Formal holds" }));
    await waitFor(() => assert.equal((screen.getByRole("button", { name: "Refresh status and evidence" }) as HTMLButtonElement).disabled, false));
}
async function draftFormal(action = "Create formal hold") {
    fireEvent.click(screen.getByRole("button", { name: action }));
    fireEvent.change(screen.getByRole("textbox", { name: "Reason" }), { target: { value: "Reviewed partner change" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /I reviewed this partner/ }));
}
function confirmFormal(action = "Create formal hold") { fireEvent.click(screen.getByRole("button", { name: `Confirm: ${action}` })); }
function formalWrite(request: Request, restored = false) {
    if (request.method !== "POST" || !request.url.pathname.startsWith("/api/v1/admin/suspensions/partners/")) return undefined;
    holdEvents = [holdEvent({ evidence: request.body?.evidence ?? null, is_active: !restored,
        restored_at: restored ? AT : null, restore_reason: restored ? "Reviewed partner change" : null })];
    return json(saved(restored));
}

for (const state of ["active", "suspended", "pending_deletion", "unknown"]) test(`candidate ${state} has one exact-target Manage suspension entry and no legacy mutation`, async () => {
    rows = [partner(state)]; await draw(); const row = await rowFor(state);
    assert.equal(row.queryAllByRole("button", { name: "Manage suspension" }).length, 1);
    assert.equal(row.queryAllByRole("button", { name: /^(Suspend|Reactivate) partner$/ }).length, 0);
    fireEvent.click(row.getByRole("button", { name: "Manage suspension" }));
    await screen.findByRole("dialog", { name: `Formal holds: Owned ${state} partner` });
    await waitFor(() => assert.equal(calls.some(call => call.url.pathname === `/api/v1/admin/suspensions/partners/${rows[0]!.id}/status`), true));
    assert.equal(writes().length, 0, "Opening review never sends either a formal or legacy mutation");
});

for (const restored of [false, true]) test(`candidate accepted formal ${restored ? "restore" : "hold"} refreshes directory and canonical authority without guessing account state`, async () => {
    rows = [partner(restored ? "suspended" : "active")];
    if (restored) {
        holdEvents = [holdEvent()];
        profile = MeResponseSchema.parse({ ...profile, partner_id: rows[0]!.id, partner_status: "suspended" });
    }
    override = request => formalWrite(request, restored);
    await draw(); await openFormal(restored ? "suspended" : "active");
    const action = restored ? "Restore selected hold" : "Create formal hold";
    await draftFormal(action); const reads = directoryCalls().length, profiles = meReads;
    confirmFormal(action); await screen.findByText("Saved decision confirmed");
    await waitFor(() => { assert.equal(directoryCalls().length > reads, true); assert.equal(meReads > profiles, true); });
    assert.equal(writes().length, 1);
    assert.equal(writes()[0]!.url.pathname, `/api/v1/admin/suspensions/partners/${rows[0]!.id}/${restored ? "restore" : "suspend"}`);
    if (restored) {
        assert.equal(writes()[0]!.body?.expected_suspension_id, HOLD);
        assert.equal(screen.getByLabelText("Canonical partner restriction").textContent, "suspended", "Exact hold restoration must not optimistically reactivate independent status");
        assert.match(document.body.textContent ?? "", /Account restriction reported with this decision: restricted/);
    }
});

for (const changed of ["search", "page"] as const) test(`candidate notification uses the current ${changed} query after a synthetic parent query change while review remains mounted`, async () => {
    rows = [partner("active")]; total = 16;
    override = request => formalWrite(request);
    await draw(); await openFormal();
    // Component-level ownership control: a query update while the modal is
    // mounted is injected directly, not claimed as a native inert-background action.
    if (changed === "search") fireEvent.change(screen.getByPlaceholderText("Partner name or ID"), { target: { value: "current query" } });
    else fireEvent.click(screen.getAllByRole("button", { name: "Next", hidden: true })[0]!);
    await waitFor(() => assert.equal(directoryCalls().some(call => changed === "search" ? call.url.searchParams.get("q") === "current query" : call.url.searchParams.get("page") === "2"), true));
    await draftFormal(); const reads = directoryCalls().length; confirmFormal(); await screen.findByText("Saved decision confirmed");
    await waitFor(() => assert.equal(directoryCalls().length > reads, true));
    const refreshed = directoryCalls().slice(reads);
    assert.equal(refreshed.every(call => changed === "search" ? call.url.searchParams.get("q") === "current query" : call.url.searchParams.get("page") === "2"), true);
    assert.equal(writes().length, 1);
});

test("candidate accepted receipt survives failed parent directory and canonical-profile refresh", async () => {
    rows = [partner("active")]; let committed = false;
    override = request => {
        const result = formalWrite(request); if (result) { committed = true; return result; }
        if (committed && request.url.pathname === "/api/v1/admin/partners") return json({ detail: "Directory refresh unavailable" }, 503);
        return undefined;
    };
    await draw(); await openFormal(); await draftFormal(); const reads = directoryCalls().length, profiles = meReads;
    api.getMe = async () => { meReads += 1; throw new Error("Synthetic canonical profile read unavailable"); };
    confirmFormal(); await screen.findByText("Saved decision confirmed");
    await waitFor(() => { assert.equal(directoryCalls().length > reads, true); assert.equal(meReads > profiles, true); });
    await screen.findByText("Partner controls unavailable");
    assert.equal(Boolean(screen.queryByText("Saved decision confirmed")), true);
    assert.doesNotMatch(document.body.textContent ?? "", /request outcome is unverified/); assert.equal(writes().length, 1);
});

test("candidate saved change refreshes canonical authority silently while its profile read is pending", async () => {
    rows = [partner("active")]; override = request => formalWrite(request);
    await draw(); await openFormal(); await draftFormal();
    const pending = deferred<ReturnType<typeof MeResponseSchema.parse>>(), profiles = meReads;
    api.getMe = () => { meReads += 1; return pending.promise; };
    try {
        confirmFormal(); await screen.findByText("Saved decision confirmed");
        await waitFor(() => assert.equal(meReads > profiles, true));
        assert.equal(screen.getByLabelText("Canonical profile loading").textContent, "false",
            "The actual AuthProvider loading flag must not trigger route-shell replacement during this refresh");
        assert.equal(Boolean(screen.queryByRole("dialog")), true);
        assert.equal(Boolean(screen.queryByText("Saved decision confirmed")), true);
        assert.equal(writes().length, 1);
    } finally { await act(async () => pending.resolve(profile)); }
});

test("candidate explicit matching saved-decision review refreshes parent once; plain status read does not", async () => {
    rows = [partner("active")];
    override = request => request.method === "POST" ? json({ detail: "Synthetic response lost after commit" }, 503) : undefined;
    await draw(); await openFormal(); await draftFormal(); confirmFormal(); await screen.findByText(/request outcome is unverified/);
    await waitFor(() => assert.equal((screen.getByRole("button", { name: "Refresh status and evidence" }) as HTMLButtonElement).disabled, false));
    holdEvents = [holdEvent({ evidence: writes()[0]!.body?.evidence })];
    const reads = directoryCalls().length, profiles = meReads;
    fireEvent.click(screen.getByRole("button", { name: "Refresh status and evidence" }));
    await waitFor(() => assert.equal((screen.getByRole("button", { name: "Review matching saved decision" }) as HTMLButtonElement).disabled, false));
    assert.equal(directoryCalls().length, reads); assert.equal(meReads, profiles);
    fireEvent.click(screen.getByRole("button", { name: "Review matching saved decision" }));
    await screen.findByText("Matching saved decision reviewed");
    await waitFor(() => { assert.equal(directoryCalls().length > reads, true); assert.equal(meReads > profiles, true); });
    const settled = directoryCalls().length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh status and evidence" }));
    await waitFor(() => assert.equal((screen.getByRole("button", { name: "Refresh status and evidence" }) as HTMLButtonElement).disabled, false));
    assert.equal(directoryCalls().length, settled); assert.equal(writes().length, 1);
    assert.doesNotMatch(document.body.textContent ?? "", /Original request receipt unavailable.*Audit receipt:/);
});

for (const retired of ["close", "identity"] as const) test(`candidate late formal response after ${retired} cannot refresh a retired parent owner`, async () => {
    rows = [partner("active")]; const pending = deferred<Response>();
    override = request => request.method === "POST" ? pending.promise : undefined;
    await draw(); await openFormal(); await draftFormal(); confirmFormal(); await waitFor(() => assert.equal(writes().length, 1));
    if (retired === "close") fireEvent.click(screen.getByRole("button", { name: "Close" }));
    else fireEvent.click(screen.getByRole("button", { name: "Replace fixture identity", hidden: true }));
    await act(async () => {}); const reads = directoryCalls().length, profiles = meReads;
    await act(async () => pending.resolve(json(saved())));
    assert.equal(directoryCalls().length, reads); assert.equal(meReads, profiles);
    assert.equal(Boolean(screen.queryByText("Saved decision confirmed")), false); assert.equal(writes().length, 1);
});

for (const role of ["platform_admin", "partner_admin"] as const) test(`candidate same QueryClient retires prior directory before replacement ${role} authorization`, async () => {
    rows = [partner("active")]; await draw(); await rowFor("active"); replacementRole = role;
    const pending = deferred<Response>(); override = request => request.url.pathname === "/api/v1/admin/partners" ? pending.promise : undefined;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Replace fixture identity" }));
        assert.equal(Boolean(screen.queryByText("Owned active partner", { exact: true })), false);
        assert.equal(Boolean(screen.queryByRole("button", { name: "Manage suspension" })), false);
        await act(async () => pending.resolve(json({ detail: "Replacement directory denied" }, 403)));
        if (role === "platform_admin") await screen.findByText("Partner controls unavailable");
    } finally { await act(async () => pending.resolve(json({ detail: "Replacement directory denied" }, 403))); }
});

test("candidate delayed directory filter cannot publish prior-filter rows or their actions", async () => {
    rows = [partner("active")]; await draw(); await rowFor("active"); const pending = deferred<Response>();
    override = request => request.url.pathname === "/api/v1/admin/partners" && request.url.searchParams.has("q") ? pending.promise : undefined;
    try {
        fireEvent.change(screen.getByPlaceholderText("Partner name or ID"), { target: { value: "does not match" } });
        assert.equal(Boolean(screen.queryByText("Owned active partner", { exact: true })), false);
        assert.equal(Boolean(screen.queryByRole("button", { name: "Manage suspension" })), false);
        assert.equal(Boolean(screen.queryByText("No partners found")), false);
        await act(async () => pending.resolve(json({ items: [], total: 0, page: 1, page_size: 8 })));
        await screen.findByText("No partners found");
    } finally { await act(async () => pending.resolve(json({ items: [], total: 0, page: 1, page_size: 8 }))); }
});

test("candidate pending canonical profile masks partner cache and actions without claiming an empty directory", async () => {
    rows = [partner("active")]; await draw(); await rowFor("active"); const pending = deferred<ReturnType<typeof MeResponseSchema.parse>>();
    api.getMe = () => pending.promise;
    try {
        fireEvent.click(screen.getByRole("button", { name: "Recheck fixture identity" }));
        assert.equal(Boolean(screen.queryByText("Owned active partner", { exact: true })), false);
        assert.equal(Boolean(screen.queryByText("Loading partners")), true);
        assert.equal(Boolean(screen.queryByText("No partners found")), false);
        assert.equal(Boolean(screen.queryByRole("button", { name: "Manage suspension" })), false);
    } finally { await act(async () => pending.resolve(profile)); }
});

test("candidate closing after a saved change removed its directory row returns focus to stable partner search", async () => {
    rows = [partner("active")]; let committed = false;
    override = request => {
        const result = formalWrite(request); if (result) { committed = true; return result; }
        if (committed && request.url.pathname === "/api/v1/admin/partners") return json({ items: [], total: 0, page: 1, page_size: 8 });
        return undefined;
    };
    await draw(); const row = await rowFor("active");
    const opener = row.queryByRole("button", { name: "Manage suspension" }) ?? row.getByRole("button", { name: "Formal holds" });
    opener.focus(); fireEvent.click(opener);
    await waitFor(() => assert.equal((screen.getByRole("button", { name: "Refresh status and evidence" }) as HTMLButtonElement).disabled, false));
    await draftFormal(); confirmFormal(); await screen.findByText("Saved decision confirmed");
    // Explicit invalidation isolates the close-return seam from the separate
    // baseline missing parent notification. The real decoder reads the changed
    // synthetic directory; no row is removed by a mock component or DOM edit.
    await act(async () => { await qc.invalidateQueries({ queryKey: ["adminPartners"] }); });
    await screen.findByText("No partners found"); assert.equal(opener.isConnected, false);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => assert.equal(Boolean(screen.queryByRole("dialog")), false));
    const search = screen.getByPlaceholderText("Partner name or ID");
    assert.equal(document.activeElement === search, true);
    assert.equal(search.isConnected, true); assert.equal(writes().length, 1);
});
