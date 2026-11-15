/**
 * FX6b Tasks 2, 3.1 and 4 on the campaign page (plan 2.3: "The hours on screen
 * match what the backend enforces."; plan 3.3: "Clear statuses: Draft,
 * Preparing, Running, Paused, Stopping, Completed, Failed.").
 *
 * GET /campaigns/{id} answers {campaign, effective_schedule, progress}. When
 * effective_schedule is there the header and the Start window show it; when
 * progress is there the status shows its phase and the page keeps re-reading
 * until the phase ends. With either one null the page reads as it did before.
 * Real page and real shared Start dialog; only layout, routing and the heavy
 * child panels are stubbed.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { campaignsInFlight, endCampaignAction } from "@/lib/campaign-control";
import { dashboardApi, type CampaignEffectiveSchedule, type CampaignProgress, type MinutesStatus } from "@/lib/dashboard-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const CAMPAIGN_ID = "11111111-2222-3333-4444-555555555555";
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: CAMPAIGN_ID }), useRouter: () => router, useSearchParams: () => new URLSearchParams() };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/smart-csv-import")) return { SmartCsvImport: nothing };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing, ActiveContactsSummary: nothing };
    if (id.includes("components/campaigns/script-card")) return { ScriptCard: nothing };
    if (id.includes("components/campaigns/live-calls-panel")) return { LiveCallsPanel: nothing };
    if (id.includes("components/campaigns/call-issues-panel")) return { CallIssuesPanel: nothing };
    if (id.includes("components/campaigns/knowledge-panel")) return { KnowledgePanel: nothing };
    if (id.includes("components/campaigns/test-agent-button")) return { TestAgentButton: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
const clients: QueryClient[] = [];
// The texts the page already had (src/lib/calling-schedule-text.ts).
const NOT_SET = "Not set. The default of 09:00–19:00 UTC, Monday–Friday applies unless your account has its own default.";
const MISSING = /Scheduling information missing/;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    for (const kind of ["starting", "stopping", "pausing"] as const) {
        for (const id of Array.from(campaignsInFlight()[kind])) endCampaignAction(kind, id);
    }
});

const base = {
    id: CAMPAIGN_ID, name: "Spring push", direction: "outbound" as const,
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
};
const plenty: MinutesStatus = { allocated: 100, used_minutes: 10, remaining_minutes: 90, unlimited: false, exhausted: false };
const SAVED = { timezone: "Asia/Karachi", time_window_start: "10:00", time_window_end: "22:00", allowed_days: [5, 6], ignore_schedule: false };

/** The answer shape of describe_schedule (backend/app/domain/services/dialer/campaign_schedule.py). */
function effective(over: Partial<CampaignEffectiveSchedule> = {}, source: "campaign" | "account" | "platform_default" = "campaign"): CampaignEffectiveSchedule {
    return {
        timezone: "Europe/London", time_window_start: "08:00", time_window_end: "17:00", allowed_days: [0, 1],
        sources: { timezone: source, time_window_start: source, time_window_end: source, allowed_days: source },
        account_rules: { configured: source === "account", timezone: "UTC", time_window_start: "09:00", time_window_end: "19:00", allowed_days: [0, 1, 2, 3, 4] },
        ignore_schedule_requested: false, ignore_schedule_allowed: true, ignore_schedule: false,
        contact_timezones: { contact_zone_first: true, phone_derived_zone: false, contacts_with_own_timezone: 0, own_timezones: [] },
        ...over,
    };
}

type Answer = { status: string; callingConfig?: Record<string, unknown> | null; schedule?: CampaignEffectiveSchedule | null; progress?: CampaignProgress | null; bare?: boolean };

/** Mocks every request the page makes; `state.answer` is what GET /campaigns/{id} says next. */
function wire(answer: Answer) {
    const state = { answer, reads: 0 };
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(api, "getCampaignKnowledge", async (id: string) => ({ campaign_id: id, knowledge_mode: "none", sources: [], tree: [] }));
    mock.method(dashboardApi, "getCampaign", async () => {
        state.reads++;
        const now = state.answer;
        const campaign = { ...base, status: now.status, calling_config: now.callingConfig };
        // `bare` is the answer shape from before these fields existed.
        return now.bare ? { campaign } : { campaign, effective_schedule: now.schedule ?? null, progress: now.progress ?? null };
    });
    mock.method(dashboardApi, "getCampaignStats", async () => ({ total_leads: 7, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }));
    mock.method(dashboardApi, "getMinutesStatus", async () => plenty);
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    return state;
}

/** Captures the page's 7 s re-read timer instead of waiting for it. */
function captureRereads() {
    const live = new Map<number, () => void | Promise<void>>();
    let next = 9000;
    const realSet = window.setInterval.bind(window);
    const realClear = window.clearInterval.bind(window);
    mock.method(window, "setInterval", ((handler: () => void, delay?: number, ...rest: unknown[]) => {
        if (delay !== 7000) return realSet(handler, delay, ...rest);
        live.set(++next, handler);
        return next;
    }) as typeof window.setInterval);
    mock.method(window, "clearInterval", ((id?: number) => {
        if (id !== undefined && live.has(id)) { live.delete(id); return; }
        realClear(id);
    }) as typeof window.clearInterval);
    return {
        running: () => live.size,
        tick: async () => { await act(async () => { for (const handler of Array.from(live.values())) await handler(); }); },
    };
}

async function mount() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
    await waitFor(() => assert.equal(screen.queryByTestId("campaign-status") !== null, true), { timeout: SLOW });
}

const header = () => screen.getByTestId("campaign-calling-schedule").textContent ?? "";
const pill = () => screen.getByTestId("campaign-status").textContent ?? "";

async function openStart() {
    await waitFor(() => assert.equal((screen.queryByRole("button", { name: "Start" }) as HTMLButtonElement | null)?.disabled, false), { timeout: SLOW });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    return within(await screen.findByRole("dialog", { name: "Start campaign" }));
}

// ── Task 2: the header ───────────────────────────────────────────────────────

for (const source of ["campaign", "account", "platform_default"] as const) {
    test(`header: the enforced hours (source ${source}) show in the "Calling schedule:" format, in place of the saved values`, async () => {
        wire({ status: "draft", callingConfig: source === "campaign" ? SAVED : null, schedule: effective({}, source) });
        await mount();
        assert.equal(header(), "Calling schedule: Europe/London · 08:00–17:00 · Mon, Tue");
        assert.equal(document.body.textContent?.includes("Asia/Karachi"), false, "the saved values are not shown beside the enforced ones");
        assert.equal(document.body.textContent?.includes(NOT_SET), false, "the Not set hedge is replaced");
    });
}

test("header: \"call anytime\" asked for but not allowed shows the enforced hours", async () => {
    wire({ status: "draft", callingConfig: { ...SAVED, ignore_schedule: true }, schedule: effective({ ignore_schedule_requested: true, ignore_schedule_allowed: false, ignore_schedule: false }) });
    await mount();
    assert.equal(header(), "Calling schedule: Europe/London · 08:00–17:00 · Mon, Tue");
});

test("header: \"call anytime\" asked for and allowed reads exactly as the page did before (the saved values)", async () => {
    wire({ status: "draft", callingConfig: { ...SAVED, ignore_schedule: true }, schedule: effective({ ignore_schedule_requested: true, ignore_schedule_allowed: true, ignore_schedule: true }) });
    await mount();
    assert.equal(header(), "Calling schedule: Asia/Karachi · 10:00–22:00 · Sat, Sun");
});

test("header: effective_schedule null reads exactly as before, saved values or the Not set hedge", async () => {
    wire({ status: "draft", callingConfig: SAVED, schedule: null });
    await mount();
    assert.equal(header(), "Calling schedule: Asia/Karachi · 10:00–22:00 · Sat, Sun");
    cleanup();
    mock.restoreAll();
    wire({ status: "draft", callingConfig: null, schedule: null });
    await mount();
    assert.equal(header(), NOT_SET);
    cleanup();
    mock.restoreAll();
    wire({ status: "draft", callingConfig: null, bare: true });
    await mount();
    assert.equal(header(), NOT_SET, "an answer without the new fields reads as before too");
});

// ── Task 3.1: the Start window opened from the campaign page ─────────────────

test("Start window from the campaign page: the enforced hours from the same answer, with no second read", async () => {
    const state = wire({ status: "draft", callingConfig: null, schedule: effective({}, "account") });
    await mount();
    // Counted before the window opens: a read made by the window itself would add one.
    const reads = state.reads;
    const dialog = await openStart();
    assert.equal(dialog.getByTestId("calling-schedule-line").textContent, "Calling schedule: Europe/London · 08:00–17:00 · Mon, Tue");
    assert.equal(dialog.queryByTestId("calling-schedule-reading") === null, true, "nothing is read again, so nothing loads");
    assert.equal(dialog.queryByText(/Calling hours are checked in each contact's own timezone/) !== null, true, "the contact-timezone rule text stays");
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 30)));
    assert.equal(state.reads, reads, "the window does not read GET /campaigns/{id} itself");
});

test("Start window from the campaign page: effective_schedule null shows the saved values and the missing warning as before", async () => {
    wire({ status: "draft", callingConfig: { timezone: "Asia/Karachi" }, schedule: null });
    await mount();
    const dialog = await openStart();
    assert.equal(dialog.getByTestId("calling-schedule-line").textContent, "Calling schedule: Asia/Karachi · Not set–Not set · Not set");
    assert.equal(dialog.queryByText(MISSING) !== null, true);
    assert.equal(dialog.queryByTestId("calling-schedule-reading") === null, true);
});

// ── Task 4: the server's phase ───────────────────────────────────────────────

test("status: preparing reads Preparing, stopping reads Stopping, pausing reads Paused", async () => {
    const cases: Array<[string, string, string]> = [
        ["running", "preparing", "Preparing"], ["stopped", "stopping", "Stopping"], ["paused", "pausing", "Paused"],
    ];
    for (const [status, phase, label] of cases) {
        wire({ status, progress: { live_calls: 2, jobs_awaiting_dispatch: 3, phase } });
        await mount();
        assert.equal(pill(), label, `${status} / ${phase}`);
        cleanup();
        mock.restoreAll();
    }
});

test("status: any other phase, a null progress or an answer without it reads as the stored status", async () => {
    const cases: Array<[Answer, string]> = [
        [{ status: "running", progress: { live_calls: 1, jobs_awaiting_dispatch: 0, phase: "running" } }, "Running"],
        [{ status: "stopped", progress: { live_calls: 0, jobs_awaiting_dispatch: 0, phase: "stopped" } }, "Stopped"],
        [{ status: "completed", progress: { live_calls: 0, jobs_awaiting_dispatch: 0, phase: "completed" } }, "Completed"],
        [{ status: "stopped", progress: null }, "Stopped"],
        [{ status: "paused", bare: true }, "Paused"],
    ];
    for (const [answer, label] of cases) {
        wire(answer);
        await mount();
        assert.equal(pill(), label);
        cleanup();
        mock.restoreAll();
    }
});

test("re-read: the page keeps reading every 7 s while stopping, shows the new phase, and stops once it ends", async () => {
    const rereads = captureRereads();
    const state = wire({ status: "stopped", progress: { live_calls: 2, jobs_awaiting_dispatch: 0, phase: "stopping" } });
    await mount();
    assert.equal(pill(), "Stopping");
    await waitFor(() => assert.equal(rereads.running(), 1), { timeout: SLOW });
    const before = state.reads;
    await rereads.tick();
    assert.equal(state.reads, before + 1, "the timer reads the campaign again");
    assert.equal(pill(), "Stopping");
    assert.equal(rereads.running(), 1, "still stopping, still reading");
    state.answer = { status: "stopped", progress: { live_calls: 0, jobs_awaiting_dispatch: 0, phase: "stopped" } };
    await rereads.tick();
    await waitFor(() => assert.equal(pill(), "Stopped"), { timeout: SLOW });
    await waitFor(() => assert.equal(rereads.running(), 0), { timeout: SLOW });
});

test("re-read: pausing keeps reading until the phase ends", async () => {
    const rereads = captureRereads();
    const state = wire({ status: "paused", progress: { live_calls: 1, jobs_awaiting_dispatch: 0, phase: "pausing" } });
    await mount();
    await waitFor(() => assert.equal(rereads.running(), 1), { timeout: SLOW });
    state.answer = { status: "paused", progress: { live_calls: 0, jobs_awaiting_dispatch: 0, phase: "paused" } };
    await rereads.tick();
    await waitFor(() => assert.equal(rereads.running(), 0), { timeout: SLOW });
    assert.equal(pill(), "Paused");
});

test("re-read: with progress null the rule is the one from before (stopped and paused are not re-read, running is)", async () => {
    for (const [status, expected] of [["stopped", 0], ["paused", 0], ["running", 1]] as Array<[string, number]>) {
        const rereads = captureRereads();
        wire({ status, progress: null });
        await mount();
        await act(() => new Promise<void>((resolve) => setTimeout(resolve, 30)));
        assert.equal(rereads.running(), expected, status);
        cleanup();
        mock.restoreAll();
    }
});
