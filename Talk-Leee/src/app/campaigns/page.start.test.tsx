import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { beginCampaignAction, campaignsInFlight, endCampaignAction } from "@/lib/campaign-control";
import { dashboardApi, type Campaign, type CampaignControlResponse, type CampaignStartOptions, type MinutesStatus } from "@/lib/dashboard-api";

// Plan 3.2 + 3.3 on the campaign list. Real page, real table, real command
// bar, real shared Start dialog, readiness gate and in-flight store;
// dashboardApi and api methods are mocked per test. Only the layout and the
// two sibling panels that fetch on their own are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/event-stream")) return { EventStream: nothing };
    if (id.includes("components/campaigns/alert-timeline")) return { AlertTimeline: nothing };
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
const START_DIALOG = "Who speaks first?";
const requestAnimationFrame = window.requestAnimationFrame;
const cancelAnimationFrame = window.cancelAnimationFrame;
const realAlert = globalThis.alert;
const realCreateObjectURL = URL.createObjectURL;
const realRevokeObjectURL = URL.revokeObjectURL;
const realOpen = window.open;
const clients: QueryClient[] = [];
const alerts: string[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    window.requestAnimationFrame = requestAnimationFrame;
    window.cancelAnimationFrame = cancelAnimationFrame;
    globalThis.alert = realAlert;
    URL.createObjectURL = realCreateObjectURL;
    URL.revokeObjectURL = realRevokeObjectURL;
    window.open = realOpen;
    alerts.length = 0;
    localStorage.clear();
    for (const kind of ["starting", "stopping", "pausing"] as const) {
        for (const id of Array.from(campaignsInFlight()[kind])) endCampaignAction(kind, id);
    }
});

function camp(id: string, name: string, status: string, extra: Record<string, unknown> = {}): Campaign {
    return {
        id, name, description: "", status, direction: "outbound", system_prompt: "", voice_id: "v",
        max_concurrent_calls: 1, total_leads: 10, calls_completed: 0, calls_failed: 0,
        created_at: "2026-01-01T00:00:00Z", ...extra,
    } as unknown as Campaign;
}

const plenty: MinutesStatus = { allocated: 100, used_minutes: 10, remaining_minutes: 90, unlimited: false, exhausted: false };

type Wiring = {
    /** Campaign id -> why its calling route is not ready. */
    notReady?: Record<string, string>;
    minutes?: MinutesStatus;
    start?: (id: string, options: CampaignStartOptions) => Promise<{ message: string; jobs_enqueued: number }>;
    stop?: (id: string) => Promise<CampaignControlResponse>;
    pause?: (id: string) => Promise<CampaignControlResponse>;
};

function control(id: string, summary: Record<string, unknown>): CampaignControlResponse {
    return { message: `Campaign ${id} stopped`, campaign: {}, termination_summary: summary } as unknown as CampaignControlResponse;
}

/** Mocks every request the page makes. `state.campaigns` is what GET /campaigns returns next. */
function wire(campaigns: Campaign[], options: Wiring = {}) {
    const state = {
        campaigns,
        starts: [] as Array<{ id: string; options: CampaignStartOptions }>,
        stops: [] as string[],
        pauses: [] as string[],
        minutesReads: 0,
        setStatus(id: string, status: string) {
            state.campaigns = state.campaigns.map((c) => (c.id === id ? { ...c, status } : c));
        },
    };
    globalThis.alert = ((message?: unknown) => { alerts.push(String(message)); }) as typeof globalThis.alert;
    mock.method(api, "request", async (request: { path: string }) => {
        const id = decodeURIComponent(request.path.split("/")[2] ?? "");
        const reason = options.notReady?.[id];
        return reason ? { ready: false, reason } : { ready: true };
    });
    mock.method(api, "getCampaignKnowledge", async (id: string) => ({ campaign_id: id, knowledge_mode: "none", sources: [], tree: [] }));
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: state.campaigns }));
    mock.method(dashboardApi, "getMinutesStatus", async () => { state.minutesReads++; return options.minutes ?? plenty; });
    mock.method(dashboardApi, "startCampaign", async (id: string, startOptions: CampaignStartOptions) => {
        state.starts.push({ id, options: startOptions });
        const result = await (options.start ? options.start(id, startOptions) : Promise.resolve({ message: "ok", jobs_enqueued: 0 }));
        state.setStatus(id, "running");
        return result;
    });
    mock.method(dashboardApi, "stopCampaign", async (id: string) => {
        state.stops.push(id);
        const result = await (options.stop ? options.stop(id) : Promise.resolve(control(id, { status: "confirmed" })));
        state.setStatus(id, "stopped");
        return result;
    });
    mock.method(dashboardApi, "pauseCampaign", async (id: string) => {
        state.pauses.push(id);
        const result = await (options.pause ? options.pause(id) : Promise.resolve(control(id, { status: "confirmed" })));
        state.setStatus(id, "paused");
        return result;
    });
    return state;
}

async function mountPage(firstRow: string) {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Page /></QueryClientProvider></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(row(firstRow) !== null, true), { timeout: SLOW });
    // Finish the table's initial preference hydration and selection-reset frames.
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
}

function row(name: string): HTMLElement | null {
    const box = screen.queryByRole("checkbox", { name: `Select ${name}` });
    return box ? (box.closest("[role='row']") as HTMLElement) : null;
}

/** The status badge in a campaign's list row. */
function badge(name: string): string {
    return row(name)!.children[2]!.textContent ?? "";
}

function select(...names: string[]) {
    for (const name of names) fireEvent.click(screen.getByRole("checkbox", { name: `Select ${name}` }));
}

function openDetails(name: string) {
    fireEvent.click(within(row(name)!).getByText(name).closest("button")!);
    return within(screen.getByRole("dialog", { name }));
}

async function closeDialogs() {
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
}

function startDialog() {
    const dialog = screen.queryByRole("dialog", { name: START_DIALOG });
    return dialog ? within(dialog) : null;
}

async function confirmButton(): Promise<HTMLButtonElement> {
    const find = () => startDialog()!.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(find().disabled, false), { timeout: SLOW });
    return find();
}

function hasButton(scope: ReturnType<typeof within>, name: string): boolean {
    return scope.queryByRole("button", { name }) !== null;
}

async function enabledButton(scope: ReturnType<typeof within>, name: string): Promise<HTMLButtonElement> {
    await waitFor(() => assert.equal((scope.getByRole("button", { name }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    return scope.getByRole("button", { name }) as HTMLButtonElement;
}

function lines(testId: string, scope: ReturnType<typeof within> = within(document.body)): string[] | null {
    const box = scope.queryByTestId(testId);
    return box ? (Array.from(box.querySelectorAll("p")) as HTMLElement[]).map((p) => p.textContent ?? "") : null;
}

async function runCommand(text: string, title: RegExp) {
    fireEvent.click(screen.getByRole("button", { name: "Ctrl + K" }));
    const input = within(screen.getByRole("dialog", { name: "Command Bar" })).getByRole("textbox");
    fireEvent.change(input, { target: { value: text } });
    await waitFor(() => assert.equal((screen.getByRole("button", { name: title }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    fireEvent.keyDown(input, { key: "Enter" });
}

test("the details modal offers the same actions and wording as the detail page", async () => {
    const expected: Array<[string, string, string[]]> = [
        ["Draft one", "draft", ["Start"]],
        ["Stopped one", "stopped", ["Start"]],
        ["Paused one", "paused", ["Resume"]],
        ["Running one", "running", ["Pause", "Stop"]],
        ["Completed one", "completed", []],
        ["Scheduled one", "scheduled", []],
        ["Cancelled one", "cancelled", []],
    ];
    wire(expected.map(([name, status], index) => camp(`c${index}`, name, status)));
    await mountPage("Draft one");
    for (const [name, , actions] of expected) {
        const details = openDetails(name);
        for (const action of ["Start", "Resume", "Pause", "Stop"]) {
            assert.equal(hasButton(details, action), actions.includes(action), `${name}: ${action}`);
        }
        await closeDialogs();
    }
});

test("every resume entry point opens the same dialog, and none of them starts a campaign before it is confirmed", async () => {
    const state = wire([camp("p1", "Paused one", "paused"), camp("p2", "Paused two", "paused"), camp("d1", "Draft one", "draft")]);
    await mountPage("Paused one");

    // 1. The details modal's Resume.
    fireEvent.click(await enabledButton(openDetails("Paused one"), "Resume"));
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.equal(startDialog()!.queryByRole("radio", { name: /AI agent speaks first/ }) !== null, true);
    assert.equal(state.starts.length, 0);
    await closeDialogs();

    // 2. Resume Selected.
    select("Paused one", "Draft one");
    fireEvent.click(screen.getByRole("button", { name: "Resume Selected" }));
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.equal(startDialog()!.queryByRole("radio", { name: /AI agent speaks first/ }) !== null, true);
    assert.equal(state.starts.length, 0);
    await closeDialogs();

    // 3. The command bar's resume.
    await runCommand("/resume", /Resume all paused campaigns/);
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.equal(state.starts.length, 0, "no entry point sent a start on its own");

    // The choice made in the dialog is the one that is sent, to every campaign.
    assert.equal((startDialog()!.getByRole("radio", { name: /AI agent speaks first/ }) as HTMLInputElement).checked, true);
    fireEvent.click(startDialog()!.getByRole("radio", { name: /Caller speaks first/ }));
    fireEvent.click(await confirmButton());
    await waitFor(() => assert.equal(state.starts.length, 2), { timeout: SLOW });
    assert.deepEqual(state.starts, [
        { id: "p1", options: { first_speaker: "user" } },
        { id: "p2", options: { first_speaker: "user" } },
    ]);
});

test("the details modal's Resume shows Preparing and Starting…, then the queued message and the backend status", async () => {
    let finish!: (value: { message: string; jobs_enqueued: number }) => void;
    const state = wire(
        [camp("p1", "Paused one", "paused", { calling_config: { batch_size: 5, call_gap_seconds: 60 } }), camp("d1", "Draft one", "draft")],
        { start: () => new Promise((resolve) => { finish = resolve; }) },
    );
    await mountPage("Paused one");
    assert.equal(badge("Paused one"), "Paused");
    const details = openDetails("Paused one");
    fireEvent.click(await enabledButton(details, "Resume"));
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    fireEvent.click(await confirmButton());

    await waitFor(() => assert.equal(badge("Paused one"), "Preparing"), { timeout: SLOW });
    assert.equal(badge("Draft one"), "Draft", "only the campaign being started reads Preparing");
    const starting = startDialog()!.getByRole("button", { name: "Starting…" }) as HTMLButtonElement;
    assert.equal(starting.querySelector("svg.animate-spin") !== null, true);
    assert.equal(starting.disabled, true);
    assert.equal(screen.getByRole("dialog", { name: "Paused one" }).textContent?.includes("Preparing"), true, "the details modal reads Preparing");
    assert.equal((details.getByRole("button", { name: "Resume" }) as HTMLButtonElement).disabled, true);
    fireEvent.click(starting);
    assert.equal(state.starts.length, 1, "a second confirm sends nothing");

    await act(async () => { finish({ message: "ok", jobs_enqueued: 42 }); });
    await waitFor(() => assert.deepEqual(lines("campaign-control-notice", details), ["42 contacts queued for calling."]), { timeout: SLOW });
    await waitFor(() => assert.equal(badge("Paused one"), "Running"), { timeout: SLOW });
    assert.equal(startDialog() === null, true);
    assert.deepEqual(state.starts, [{ id: "p1", options: { first_speaker: "agent", batch_size: 5, call_gap_seconds: 60 } }]);
    assert.equal(hasButton(details, "Pause") && hasButton(details, "Stop"), true, "the running campaign now offers Pause and Stop");
    assert.equal(lines("campaign-list-notice"), null);
});

test("Resume Selected uses one choice for every selected campaign, skips those that cannot start and lists them", async () => {
    const state = wire(
        [camp("a", "Alpha", "paused"), camp("b", "Bravo", "draft"), camp("c", "Charlie", "stopped"), camp("d", "Delta", "running"), camp("e", "Echo", "paused")],
        {
            notReady: { b: "Selected trunk unavailable" },
            start: async (id) => {
                if (id === "e") throw Object.assign(new Error("Conflict"), { details: { message: "This campaign has no contacts to call." } });
                return { message: "ok", jobs_enqueued: id === "a" ? 30 : 12 };
            },
        },
    );
    await mountPage("Alpha");
    select("Alpha", "Bravo", "Charlie", "Delta", "Echo");
    fireEvent.click(screen.getByRole("button", { name: "Resume Selected" }));
    await waitFor(() => assert.deepEqual(lines("campaign-start-skipped", startDialog()!), [
        "Bravo: not started — Selected trunk unavailable",
        "Delta: not started — Running",
    ]), { timeout: SLOW });
    assert.equal(startDialog()!.queryByText("Calls at a time (batch size)") === null, true, "pacing stays each campaign's own");
    assert.equal(state.starts.length, 0);

    fireEvent.click(await confirmButton());
    await waitFor(() => assert.equal(lines("campaign-list-notice") !== null, true), { timeout: SLOW });
    assert.deepEqual(state.starts, [
        { id: "a", options: { first_speaker: "agent" } },
        { id: "c", options: { first_speaker: "agent" } },
        { id: "e", options: { first_speaker: "agent" } },
    ], "the running campaign and the one that cannot start were never sent");
    assert.deepEqual(lines("campaign-list-notice"), [
        "42 contacts queued for calling.",
        "Bravo: not started — Selected trunk unavailable",
        "Delta: not started — Running",
        "Echo: not started — This campaign has no contacts to call.",
    ]);
    await waitFor(() => assert.equal(badge("Alpha"), "Running"), { timeout: SLOW });
    assert.equal(badge("Bravo"), "Draft");
    assert.equal(alerts.length, 0);
});

test("Resume Selected lists every selected campaign that is not draft, paused or stopped with its status label", async () => {
    const state = wire([
        camp("a", "Alpha", "paused"), camp("r", "Summer Promo", "running"), camp("c", "Charlie", "completed"), camp("s", "Sierra", "scheduled"),
    ], { start: async () => ({ message: "ok", jobs_enqueued: 7 }) });
    await mountPage("Alpha");
    select("Alpha", "Summer Promo", "Charlie", "Sierra");
    fireEvent.click(screen.getByRole("button", { name: "Resume Selected" }));
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.deepEqual(lines("campaign-start-skipped", startDialog()!), [
        "Summer Promo: not started — Running",
        "Charlie: not started — Completed",
        "Sierra: not started — Scheduled",
    ]);
    assert.equal(state.starts.length, 0);

    fireEvent.click(await confirmButton());
    await waitFor(() => assert.deepEqual(lines("campaign-list-notice"), [
        "7 contacts queued for calling.",
        "Summer Promo: not started — Running",
        "Charlie: not started — Completed",
        "Sierra: not started — Scheduled",
    ]), { timeout: SLOW });
    assert.deepEqual(state.starts, [{ id: "a", options: { first_speaker: "agent" } }], "only the paused campaign was sent");
});

test("a set never shows or sends pacing, even with one campaign in it; a campaign's own Start keeps it", async () => {
    const pacing = ["Calls at a time (batch size)", "Wait between calls"];
    const shown = () => pacing.map((text) => startDialog()!.queryByText(text) !== null);
    const state = wire([
        camp("p1", "Paused one", "paused", { calling_config: { batch_size: 5, call_gap_seconds: 60 } }),
        camp("d1", "Draft one", "draft", { calling_config: { batch_size: 7, call_gap_seconds: 30 } }),
    ]);
    await mountPage("Paused one");

    // The command bar's resume, with one paused campaign.
    await runCommand("/resume", /Resume all paused campaigns/);
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.deepEqual(shown(), [false, false], "command bar: no pacing inputs");
    await closeDialogs();

    // Resume Selected, with one campaign selected.
    select("Paused one");
    fireEvent.click(screen.getByRole("button", { name: "Resume Selected" }));
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.deepEqual(shown(), [false, false], "Resume Selected: no pacing inputs");
    fireEvent.click(await confirmButton());
    await waitFor(() => assert.equal(state.starts.length, 1), { timeout: SLOW });
    assert.deepEqual(state.starts[0], { id: "p1", options: { first_speaker: "agent" } }, "no pacing was sent");
    await waitFor(() => assert.equal(startDialog() === null, true), { timeout: SLOW });

    // A campaign's own Start (details modal) still shows the inputs and sends them.
    fireEvent.click(await enabledButton(openDetails("Draft one"), "Start"));
    await waitFor(() => assert.equal(startDialog() !== null, true), { timeout: SLOW });
    assert.deepEqual(shown(), [true, true], "single-campaign Start: pacing inputs");
    fireEvent.click(await confirmButton());
    await waitFor(() => assert.equal(state.starts.length, 2), { timeout: SLOW });
    assert.deepEqual(state.starts[1], { id: "d1", options: { first_speaker: "agent", batch_size: 7, call_gap_seconds: 30 } });
});

test("Resume Selected checks plan minutes: out of minutes, every campaign is listed and nothing is sent", async () => {
    const state = wire([camp("a", "Alpha", "paused"), camp("b", "Bravo", "draft")], {
        minutes: { allocated: 100, used_minutes: 100, remaining_minutes: 0, unlimited: false, exhausted: true },
    });
    await mountPage("Alpha");
    select("Alpha", "Bravo");
    fireEvent.click(screen.getByRole("button", { name: "Resume Selected" }));
    const reason = "You're out of plan minutes (100/100 used this month). Add minutes or upgrade your plan to start campaigns.";
    await waitFor(() => assert.deepEqual(lines("campaign-start-skipped", startDialog()!), [
        `Alpha: not started — ${reason}`,
        `Bravo: not started — ${reason}`,
    ]), { timeout: SLOW });
    const confirm = startDialog()!.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement;
    assert.equal(confirm.disabled, true);
    fireEvent.click(confirm);
    assert.equal(state.starts.length, 0);
    assert.equal(state.minutesReads, 1, "the dialog read the minutes status when it opened");
});

test("a start already running elsewhere reads Preparing in the list and cannot be started again from the details modal", async () => {
    const state = wire([camp("p1", "Paused one", "paused")]);
    await mountPage("Paused one");
    const details = openDetails("Paused one");
    await enabledButton(details, "Resume");
    // The campaign detail page (another entry point) holds the shared in-flight id.
    act(() => { assert.equal(beginCampaignAction("starting", "p1"), true); });
    await waitFor(() => assert.equal(badge("Paused one"), "Preparing"), { timeout: SLOW });
    const resume = details.getByRole("button", { name: "Resume" }) as HTMLButtonElement;
    assert.equal(resume.disabled, true);
    fireEvent.click(resume);
    assert.equal(startDialog() === null, true);
    await closeDialogs();

    // Resume Selected still opens the dialog, but its start for this id is ignored.
    select("Paused one");
    fireEvent.click(screen.getByRole("button", { name: "Resume Selected" }));
    fireEvent.click(await confirmButton());
    await waitFor(() => assert.equal(startDialog() === null, true), { timeout: SLOW });
    assert.equal(state.starts.length, 0, "the second start of the same campaign was ignored");
});

test("Pause Selected pauses only running campaigns, lists the others, and shows Pausing… while it runs", async () => {
    let finish!: (value: CampaignControlResponse) => void;
    const state = wire(
        [camp("r", "Romeo", "running"), camp("a", "Alpha", "paused"), camp("b", "Bravo", "draft")],
        { pause: () => new Promise((resolve) => { finish = resolve; }) },
    );
    await mountPage("Romeo");
    select("Romeo", "Alpha", "Bravo");
    fireEvent.click(screen.getByRole("button", { name: "Pause Selected" }));
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Pausing…" }) !== null, true), { timeout: SLOW });
    const pausing = screen.getByRole("button", { name: "Pausing…" }) as HTMLButtonElement;
    assert.equal(pausing.disabled, true);
    assert.equal(pausing.querySelector("svg.animate-spin") !== null, true);
    fireEvent.click(pausing);

    await act(async () => { finish(control("r", { status: "confirmed" })); });
    await waitFor(() => assert.deepEqual(lines("campaign-list-notice"), [
        "All live calls were ended.",
        "Alpha: not paused — not running",
        "Bravo: not paused — not running",
    ]), { timeout: SLOW });
    assert.deepEqual(state.pauses, ["r"], "only the running campaign was paused, once");
    await waitFor(() => assert.equal(badge("Romeo"), "Paused"), { timeout: SLOW });
    assert.equal(alerts.length, 0);
});

test("the command bar's pause acts only on running campaigns", async () => {
    const state = wire([camp("r", "Romeo", "running"), camp("a", "Alpha", "paused")], {
        pause: async (id) => control(id, { status: "partial", deferred: 2 }),
    });
    await mountPage("Romeo");
    await runCommand("/pause", /Pause all active campaigns/);
    await waitFor(() => assert.deepEqual(lines("campaign-list-notice"), ["2 live calls are still ending."]), { timeout: SLOW });
    assert.deepEqual(state.pauses, ["r"]);
});

test("the details modal has Stop for a running campaign: Stopping while it runs, then the same notice as the detail page", async () => {
    const id = "11111111-2222-3333-4444-555555555555";
    let finish!: (value: CampaignControlResponse) => void;
    const state = wire([camp(id, "Romeo", "running")], { stop: () => new Promise((resolve) => { finish = resolve; }) });
    await mountPage("Romeo");
    const details = openDetails("Romeo");
    fireEvent.click(await enabledButton(details, "Stop"));
    await waitFor(() => assert.equal(badge("Romeo"), "Stopping"), { timeout: SLOW });
    const stopping = details.getByRole("button", { name: "Stopping…" }) as HTMLButtonElement;
    assert.equal(stopping.querySelector("svg.animate-spin") !== null, true);
    assert.equal(stopping.disabled, true);
    assert.equal((details.getByRole("button", { name: "Pause" }) as HTMLButtonElement).disabled, true);
    assert.equal(screen.getByRole("dialog", { name: "Romeo" }).textContent?.includes("Stopping"), true);
    fireEvent.click(stopping);

    await act(async () => { finish(control(id, { status: "partial", deferred: 3 })); });
    await waitFor(() => assert.deepEqual(lines("campaign-control-notice", details), ["3 live calls are still ending."]), { timeout: SLOW });
    assert.deepEqual(state.stops, [id], "one stop request");
    await waitFor(() => assert.equal(badge("Romeo"), "Stopped"), { timeout: SLOW });
    assert.equal(hasButton(details, "Start"), true, "a stopped campaign offers Start");
    assert.equal(alerts.length, 0, "no native alert()");
    assert.equal((document.body.textContent ?? "").includes(id), false, "the raw campaign id is never shown");
});

test("the details modal's Stop and Pause show each termination sentence, and Pausing… while Pause runs", async () => {
    const cases: Array<[Record<string, unknown>, string]> = [
        [{ status: "confirmed" }, "All live calls were ended."],
        [{ status: "lookup_failed" }, "Live calls could not be checked. Some may still be ending."],
    ];
    for (const [summary, text] of cases) {
        wire([camp("r", "Romeo", "running")], { stop: async (id) => control(id, summary) });
        await mountPage("Romeo");
        const details = openDetails("Romeo");
        fireEvent.click(await enabledButton(details, "Stop"));
        await waitFor(() => assert.deepEqual(lines("campaign-control-notice", details), [text]), { timeout: SLOW });
        cleanup();
        mock.restoreAll();
    }

    let finish!: (value: CampaignControlResponse) => void;
    const state = wire([camp("r", "Romeo", "running")], { pause: () => new Promise((resolve) => { finish = resolve; }) });
    await mountPage("Romeo");
    const details = openDetails("Romeo");
    fireEvent.click(await enabledButton(details, "Pause"));
    await waitFor(() => assert.equal(hasButton(details, "Pausing…"), true), { timeout: SLOW });
    const pausing = details.getByRole("button", { name: "Pausing…" }) as HTMLButtonElement;
    assert.equal(pausing.querySelector("svg.animate-spin") !== null, true);
    assert.equal(pausing.disabled, true);
    assert.equal((details.getByRole("button", { name: "Stop" }) as HTMLButtonElement).disabled, true);
    fireEvent.click(pausing);
    await act(async () => { finish(control("r", { status: "confirmed" })); });
    await waitFor(() => assert.deepEqual(lines("campaign-control-notice", details), ["All live calls were ended."]), { timeout: SLOW });
    assert.deepEqual(state.pauses, ["r"]);
    assert.equal(hasButton(details, "Resume"), true, "a paused campaign offers Resume");
    assert.equal(alerts.length, 0);
});

const labelled: Array<[string, string, string]> = [
    ["Draft one", "draft", "Draft"],
    ["Running one", "running", "Running"],
    ["Paused one", "paused", "Paused"],
    ["Completed one", "completed", "Completed"],
    ["Stopped one", "stopped", "Stopped"],
    ["Scheduled one", "scheduled", "Scheduled"],
    ["Cancelled one", "cancelled", "Cancelled"],
];

test("the list badge, the details modal and the status filter all use the shared label", async () => {
    wire(labelled.map(([name, status], index) => camp(`c${index}`, name, status)));
    await mountPage("Draft one");
    for (const [name, , label] of labelled) {
        assert.equal(badge(name), label, `${name}: list badge`);
        openDetails(name);
        const description = screen.getByRole("dialog", { name }).getAttribute("aria-describedby")!;
        assert.equal(document.getElementById(description)!.textContent, label, `${name}: details modal`);
        await closeDialogs();
    }
    fireEvent.click(screen.getByRole("button", { name: /All statuses/ }));
    const options = Array.from(screen.getByRole("listbox").querySelectorAll("label")).map((label) => label.textContent);
    assert.deepEqual(options, ["Draft", "Running", "Paused", "Completed", "Stopped", "Scheduled", "Cancelled"]);
    assert.equal(options.includes("Active") || options.includes("Failed"), false);

    // Filtering by a label keeps exactly the campaigns that show it.
    fireEvent.click(within(screen.getByRole("listbox")).getByRole("checkbox", { name: "Stopped" }));
    await waitFor(() => assert.equal(row("Running one") === null, true), { timeout: SLOW });
    assert.equal(row("Stopped one") !== null, true);
});

test("a status filter saved under the old labels still matches after the rename", async () => {
    localStorage.setItem("campaigns.performance.filters", JSON.stringify({ statuses: ["Active", "Failed"], successMin: 0, successMax: 100, query: "" }));
    wire(labelled.map(([name, status], index) => camp(`c${index}`, name, status)));
    await mountPage("Running one");
    await waitFor(() => assert.equal(row("Draft one") === null, true), { timeout: SLOW });
    assert.equal(row("Running one") !== null && row("Stopped one") !== null, true);
    assert.equal(row("Paused one") === null, true);
});

test("the CSV, JSON and print exports use the shared label", async () => {
    wire(labelled.map(([name, status], index) => camp(`c${index}`, name, status)));
    const blobs: Blob[] = [];
    let printed = "";
    URL.createObjectURL = (blob: Blob | MediaSource) => { blobs.push(blob as Blob); return "blob:test"; };
    URL.revokeObjectURL = () => {};
    window.open = (() => ({ document: { write(html: string) { printed += html; }, close() {} }, focus() {}, print() {} })) as unknown as typeof window.open;
    await mountPage("Draft one");
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    const dialog = within(screen.getByRole("dialog", { name: "Export & Reporting" }));
    const wanted = labelled.map(([, , label]) => label);

    fireEvent.click(dialog.getByRole("button", { name: "CSV" }));
    const csv = (await blobs[0]!.text()).split("\n");
    const statusColumn = csv[0]!.split(",").indexOf("status");
    assert.deepEqual(csv.slice(1).map((line) => line.split(",")[statusColumn]), wanted);

    fireEvent.click(dialog.getByRole("button", { name: "JSON" }));
    const json = JSON.parse(await blobs[1]!.text()) as { items: Array<{ status: string }> };
    assert.deepEqual(json.items.map((item) => item.status), wanted);

    fireEvent.click(dialog.getByRole("button", { name: "PDF" }));
    for (const label of wanted) assert.equal(printed.includes(`<td>${label}</td>`), true, `print view: ${label}`);
    assert.equal(/<td>(Active|Failed|running|stopped)<\/td>/.test(printed), false);
});
