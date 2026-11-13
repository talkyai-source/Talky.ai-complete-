import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { beginCampaignAction, campaignsInFlight, endCampaignAction } from "@/lib/campaign-control";
import { dashboardApi, type CampaignControlResponse, type CampaignStartOptions, type MinutesStatus } from "@/lib/dashboard-api";

// Plan 3.2 + 3.3 on the campaign detail page. Real page, real shared Start
// dialog, real readiness gate and real in-flight store; dashboardApi and
// api methods are mocked per test. Only layout, routing and the heavy child
// panels (which fetch on their own) are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const CAMPAIGN_ID = "11111111-2222-3333-4444-555555555555";
// Stable identity like the real router: loadData depends on it.
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

// Generous: the full suite runs many files in parallel on a loaded machine.
const SLOW = 15_000;
const clients: QueryClient[] = [];
const alerts: string[] = [];
const realAlert = globalThis.alert;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    globalThis.alert = realAlert;
    alerts.length = 0;
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

type Wiring = {
    start?: (id: string, options: CampaignStartOptions) => Promise<{ message: string; jobs_enqueued: number }>;
    stop?: () => Promise<CampaignControlResponse>;
    pause?: () => Promise<CampaignControlResponse>;
    minutes?: MinutesStatus;
    callingConfig?: Record<string, unknown>;
};

/** Mocks every request the page makes. `state.status` is what the backend reports next. */
function wire(status: string, options: Wiring = {}) {
    const state = { status, starts: [] as Array<{ id: string; options: CampaignStartOptions }>, stops: 0, pauses: 0 };
    globalThis.alert = ((message?: unknown) => { alerts.push(String(message)); }) as typeof globalThis.alert;
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(api, "getCampaignKnowledge", async (id: string) => ({ campaign_id: id, knowledge_mode: "none", sources: [], tree: [] }));
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign: { ...base, status: state.status, calling_config: options.callingConfig } }));
    mock.method(dashboardApi, "getCampaignStats", async () => ({ total_leads: 7, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }));
    mock.method(dashboardApi, "getMinutesStatus", async () => options.minutes ?? plenty);
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    mock.method(dashboardApi, "startCampaign", async (id: string, startOptions: CampaignStartOptions) => {
        state.starts.push({ id, options: startOptions });
        return options.start ? options.start(id, startOptions) : { message: "ok", jobs_enqueued: 0 };
    });
    mock.method(dashboardApi, "stopCampaign", async () => {
        state.stops++;
        return options.stop ? options.stop() : control({ status: "confirmed" });
    });
    mock.method(dashboardApi, "pauseCampaign", async () => {
        state.pauses++;
        return options.pause ? options.pause() : control({ status: "confirmed" });
    });
    return state;
}

function control(summary: Record<string, unknown>): CampaignControlResponse {
    return { message: `Campaign ${CAMPAIGN_ID} stopped`, campaign: {}, termination_summary: summary } as unknown as CampaignControlResponse;
}

async function mount() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
    await waitFor(() => assert.equal(screen.queryByTestId("campaign-status") !== null, true), { timeout: SLOW });
}

function pill(): string {
    return screen.getByTestId("campaign-status").textContent ?? "";
}

function button(name: string | RegExp): HTMLButtonElement | null {
    return screen.queryByRole("button", { name }) as HTMLButtonElement | null;
}

async function readyButton(name: string): Promise<HTMLButtonElement> {
    await waitFor(() => assert.equal(button(name)?.disabled, false), { timeout: SLOW });
    return button(name)!;
}

function startDialog() {
    const dialog = screen.queryByRole("dialog", { name: "Start campaign" });
    return dialog ? within(dialog) : null;
}

function noticeText(): string | null {
    return screen.queryByTestId("campaign-control-notice")?.textContent ?? null;
}

test("the action reads Start for draft and stopped, Resume for paused, and is offered for exactly those", async () => {
    const expected: Array<[string, string | null]> = [
        ["draft", "Start"], ["stopped", "Start"], ["paused", "Resume"],
        ["running", null], ["completed", null], ["scheduled", null], ["cancelled", null],
    ];
    for (const [status, label] of expected) {
        wire(status);
        await mount();
        assert.equal(button("Start") !== null, label === "Start", `${status}: Start`);
        assert.equal(button("Resume") !== null, label === "Resume", `${status}: Resume`);
        assert.equal(button("Pause") !== null, status === "running", `${status}: Pause`);
        assert.equal(button("Stop") !== null, status === "running", `${status}: Stop`);
        cleanup();
        mock.restoreAll();
    }
});

test("the status pill shows the shared label, never the raw value", async () => {
    const expected: Array<[string, string]> = [
        ["draft", "Draft"], ["running", "Running"], ["paused", "Paused"], ["completed", "Completed"],
        ["stopped", "Stopped"], ["scheduled", "Scheduled"], ["cancelled", "Cancelled"], ["failed", "Failed"],
    ];
    for (const [status, label] of expected) {
        wire(status);
        await mount();
        assert.equal(pill(), label);
        cleanup();
        mock.restoreAll();
    }
});

test("Start opens the shared dialog and sends nothing until it is confirmed, then sends the choice made there", async () => {
    const state = wire("paused", { callingConfig: { batch_size: 20, call_gap_seconds: 30 } });
    await mount();
    assert.equal(startDialog() === null, true);
    fireEvent.click(await readyButton("Resume"));
    const dialog = startDialog()!;
    assert.equal(state.starts.length, 0, "opening the dialog starts nothing");
    const agent = dialog.getByRole("radio", { name: /AI agent speaks first/ }) as HTMLInputElement;
    const caller = dialog.getByRole("radio", { name: /Callee speaks first/ }) as HTMLInputElement;
    assert.equal(agent.checked, true, "the choice starts on agent");
    fireEvent.click(caller);
    await waitFor(() => assert.equal((dialog.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    fireEvent.click(dialog.getByRole("button", { name: "Start campaign" }));
    await waitFor(() => assert.equal(state.starts.length, 1), { timeout: SLOW });
    assert.deepEqual(state.starts[0], { id: CAMPAIGN_ID, options: { first_speaker: "user", batch_size: 20, call_gap_seconds: 30 } });
});

test("the first-speaker choice goes back to agent every time the dialog opens", async () => {
    wire("draft");
    await mount();
    fireEvent.click(await readyButton("Start"));
    fireEvent.click(startDialog()!.getByRole("radio", { name: /Callee speaks first/ }));
    fireEvent.click(startDialog()!.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(startDialog() === null, true), { timeout: SLOW });
    fireEvent.click(await readyButton("Start"));
    assert.equal((startDialog()!.getByRole("radio", { name: /AI agent speaks first/ }) as HTMLInputElement).checked, true);
});

test("the dialog carries the readiness gate, the calling-schedule notice and the pacing controls", async () => {
    wire("draft");
    await mount();
    fireEvent.click(await readyButton("Start"));
    const dialog = startDialog()!;
    assert.equal(dialog.queryByTestId("calling-schedule-line") !== null, true);
    assert.equal(dialog.queryByText("Calls at a time (batch size)") !== null, true);
    assert.equal(dialog.queryByText("Wait between calls") !== null, true);
});

test("from the confirm until the response the campaign reads Preparing and the button shows Starting…", async () => {
    let finish!: (value: { message: string; jobs_enqueued: number }) => void;
    const state = wire("paused", { start: () => new Promise((resolve) => { finish = resolve; }) });
    await mount();
    fireEvent.click(await readyButton("Resume"));
    const dialog = startDialog()!;
    await waitFor(() => assert.equal((dialog.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    fireEvent.click(dialog.getByRole("button", { name: "Start campaign" }));
    await waitFor(() => assert.equal(pill(), "Preparing"), { timeout: SLOW });
    const confirm = dialog.getByRole("button", { name: "Starting…" }) as HTMLButtonElement;
    assert.equal(confirm.querySelector("svg.animate-spin") !== null, true, "a spinner shows next to Starting…");
    assert.equal(confirm.disabled, true);
    assert.equal(button("Resume")!.disabled, true, "the page's Resume is held while the start runs");
    fireEvent.click(confirm);
    assert.equal(state.starts.length, 1, "a second confirm sends nothing");
    assert.equal(noticeText(), null);

    state.status = "running";
    await act(async () => { finish({ message: "ok", jobs_enqueued: 12 }); });
    await waitFor(() => assert.equal(noticeText(), "12 contacts queued for calling."), { timeout: SLOW });
    await waitFor(() => assert.equal(pill(), "Running"), { timeout: SLOW });
    assert.equal(startDialog() === null, true, "the dialog closed");
    assert.equal(state.starts.length, 1);
    assert.equal(alerts.length, 0);
});

test("a start already running from another entry point is not started again from this page", async () => {
    const state = wire("paused");
    await mount();
    await readyButton("Resume");
    // The campaign list (or another tab's dialog) holds the shared in-flight id.
    act(() => { assert.equal(beginCampaignAction("starting", CAMPAIGN_ID), true); });
    await waitFor(() => assert.equal(pill(), "Preparing"), { timeout: SLOW });
    assert.equal(button("Resume")!.disabled, true);
    fireEvent.click(button("Resume")!);
    assert.equal(startDialog() === null, true);
    assert.equal(state.starts.length, 0);
    act(() => { endCampaignAction("starting", CAMPAIGN_ID); });
    await waitFor(() => assert.equal(pill(), "Paused"), { timeout: SLOW });
});

test("a start confirmed while another entry point's start is still running sends no second request", async () => {
    const state = wire("paused");
    await mount();
    fireEvent.click(await readyButton("Resume"));
    const dialog = startDialog()!;
    await waitFor(() => assert.equal((dialog.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    // The same campaign's start begins elsewhere while this dialog is open.
    act(() => { beginCampaignAction("starting", CAMPAIGN_ID); });
    fireEvent.click(dialog.getByRole("button", { name: "Start campaign" }));
    await waitFor(() => assert.equal(startDialog() === null, true), { timeout: SLOW });
    assert.equal(state.starts.length, 0, "ignored: the first start is still running");
    assert.equal(noticeText(), null);
});

test("a refused start shows the server's own message and the campaign keeps its backend status", async () => {
    wire("draft", { start: async () => { throw Object.assign(new Error("Payment Required"), { details: { message: "You have used all 100 plan minutes." } }); } });
    await mount();
    fireEvent.click(await readyButton("Start"));
    const dialog = startDialog()!;
    await waitFor(() => assert.equal((dialog.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    fireEvent.click(dialog.getByRole("button", { name: "Start campaign" }));
    await waitFor(() => assert.equal(screen.queryByText("You have used all 100 plan minutes.") !== null, true), { timeout: SLOW });
    assert.equal(pill(), "Draft");
    assert.equal(noticeText(), null);
    assert.equal(alerts.length, 0);
});

test("out of plan minutes: Start is held and the dialog never opens", async () => {
    const state = wire("draft", { minutes: { allocated: 100, used_minutes: 100, remaining_minutes: 0, unlimited: false, exhausted: true } });
    await mount();
    await waitFor(() => assert.equal(screen.queryByText("Out of plan minutes") !== null, true), { timeout: SLOW });
    assert.equal(button("Start")!.disabled, true);
    fireEvent.click(button("Start")!);
    assert.equal(startDialog() === null, true);
    assert.equal(state.starts.length, 0);
});

test("while Stop runs the campaign reads Stopping and the button shows Stopping…", async () => {
    let finish!: (value: CampaignControlResponse) => void;
    const state = wire("running", { stop: () => new Promise((resolve) => { finish = resolve; }) });
    await mount();
    fireEvent.click(await readyButton("Stop"));
    await waitFor(() => assert.equal(pill(), "Stopping"), { timeout: SLOW });
    const stopping = button("Stopping…")!;
    assert.equal(stopping.querySelector("svg.animate-spin") !== null, true);
    assert.equal(stopping.disabled, true);
    assert.equal(button("Pause")!.disabled, true);
    fireEvent.click(stopping);
    assert.equal(state.stops, 1, "a second Stop sends nothing");

    state.status = "stopped";
    await act(async () => { finish(control({ status: "confirmed" })); });
    await waitFor(() => assert.equal(noticeText(), "All live calls were ended."), { timeout: SLOW });
    assert.equal(pill(), "Stopped");
    assert.equal(button("Start") !== null, true, "a stopped campaign offers Start");
});

test("Stop reports each termination summary inline with its exact sentence, never an alert or the campaign id", async () => {
    const cases: Array<[Record<string, unknown>, string, string]> = [
        [{ status: "confirmed" }, "All live calls were ended.", "success"],
        [{ status: "partial", deferred: 3 }, "3 live calls are still ending.", "warning"],
        [{ status: "lookup_failed" }, "Live calls could not be checked. Some may still be ending.", "warning"],
    ];
    for (const [summary, text, kind] of cases) {
        const state = wire("running", { stop: async () => control(summary) });
        await mount();
        fireEvent.click(await readyButton("Stop"));
        await waitFor(() => assert.equal(noticeText(), text), { timeout: SLOW });
        const notice = screen.getByTestId("campaign-control-notice");
        assert.equal(notice.getAttribute("role"), "status");
        assert.equal(notice.getAttribute("data-kind"), kind);
        assert.equal(state.stops, 1);
        assert.equal(alerts.length, 0, "no native alert()");
        assert.equal((document.body.textContent ?? "").includes(CAMPAIGN_ID), false, "the raw campaign id is never shown");
        cleanup();
        mock.restoreAll();
    }
});

test("while Pause runs the button shows Pausing…, then the same inline notice, never an alert", async () => {
    let finish!: (value: CampaignControlResponse) => void;
    const state = wire("running", { pause: () => new Promise((resolve) => { finish = resolve; }) });
    await mount();
    fireEvent.click(await readyButton("Pause"));
    await waitFor(() => assert.equal(button("Pausing…") !== null, true), { timeout: SLOW });
    const pausing = button("Pausing…")!;
    assert.equal(pausing.querySelector("svg.animate-spin") !== null, true);
    assert.equal(pausing.disabled, true);
    assert.equal(button("Stop")!.disabled, true);
    fireEvent.click(pausing);
    assert.equal(state.pauses, 1, "a second Pause sends nothing");

    state.status = "paused";
    await act(async () => { finish(control({ status: "partial", deferred: 2 })); });
    await waitFor(() => assert.equal(noticeText(), "2 live calls are still ending."), { timeout: SLOW });
    assert.equal(pill(), "Paused");
    assert.equal(button("Resume") !== null, true, "a paused campaign offers Resume");
    assert.equal(alerts.length, 0);
    assert.equal((document.body.textContent ?? "").includes(CAMPAIGN_ID), false);
});

test("a refused Stop or Pause shows the server's message inline, never an alert", async () => {
    wire("running", {
        stop: async () => { throw new Error("Campaign cannot be stopped right now"); },
        pause: async () => { throw new Error("Campaign cannot be paused right now"); },
    });
    await mount();
    fireEvent.click(await readyButton("Stop"));
    await waitFor(() => assert.equal(noticeText(), "Campaign cannot be stopped right now"), { timeout: SLOW });
    assert.equal(screen.getByTestId("campaign-control-notice").getAttribute("role"), "alert");
    fireEvent.click(await readyButton("Pause"));
    await waitFor(() => assert.equal(noticeText(), "Campaign cannot be paused right now"), { timeout: SLOW });
    assert.equal(pill(), "Running");
    assert.equal(alerts.length, 0);
});
