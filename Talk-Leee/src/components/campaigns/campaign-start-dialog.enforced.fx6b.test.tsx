/**
 * FX6b Task 3.2 (plan 2.3: "The hours on screen match what the backend
 * enforces."). Opened from the campaigns list the Start window has only the
 * saved values, so it reads each shown campaign's GET /campaigns/{id} when it
 * opens and shows the enforced hours where the saved values showed. While the
 * read runs that place holds the window's spinner; a failed read, or an answer
 * without effective_schedule, shows the saved values exactly as before.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { api } from "@/lib/api";
import { dashboardApi, type Campaign, type CampaignDetailAnswer, type CampaignEffectiveSchedule } from "@/lib/dashboard-api";
import { CampaignStartDialog, useCampaignStartDialog, type CampaignStartTarget } from "./campaign-start-dialog";

const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); });

const SAVED = { timezone: "Asia/Karachi", time_window_start: "10:00", time_window_end: "22:00", allowed_days: [5, 6], ignore_schedule: false };
const SAVED_LINE = "Calling schedule: Asia/Karachi · 10:00–22:00 · Sat, Sun";
const campaign = (id: string, name: string, status = "paused", calling_config: unknown = SAVED) =>
    ({ id, name, status, direction: "outbound", calling_config }) as unknown as Campaign;
const enforced = (timezone: string, over: Partial<CampaignEffectiveSchedule> = {}): CampaignEffectiveSchedule => ({
    timezone, time_window_start: "08:00", time_window_end: "17:00", allowed_days: [0, 1],
    ignore_schedule_requested: false, ignore_schedule_allowed: true, ignore_schedule: false, ...over,
});

function Host({ targets, listSkipped }: { targets: CampaignStartTarget[]; listSkipped: boolean }) {
    const dialog = useCampaignStartDialog();
    return <><button onClick={() => dialog.open(targets, { listSkipped })}>open</button><CampaignStartDialog dialog={dialog} /></>;
}

/** Opens the window as the campaigns list does; every GET /campaigns/{id} waits for `answer(id, …)`. */
async function open(targets: CampaignStartTarget[], listSkipped = true) {
    const waiting = new Map<string, { resolve: (value: CampaignDetailAnswer) => void; reject: (reason: Error) => void }>();
    const reads: string[] = [];
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(dashboardApi, "getMinutesStatus", async () => ({ allocated: 100, used_minutes: 0, remaining_minutes: 100, unlimited: false, exhausted: false }));
    mock.method(dashboardApi, "getCampaign", (id: string) => {
        reads.push(id);
        return new Promise<CampaignDetailAnswer>((resolve, reject) => { waiting.set(id, { resolve, reject }); });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Host targets={targets} listSkipped={listSkipped} /></QueryClientProvider></AppRouterContext.Provider>);
    await act(async () => { screen.getByRole("button", { name: "open" }).click(); });
    const dialog = within(await screen.findByRole("dialog", { name: "Start campaign" }));
    return {
        dialog, reads,
        answer: (id: string, value: CampaignDetailAnswer) => act(async () => { waiting.get(id)!.resolve(value); }),
        fail: (id: string) => act(async () => { waiting.get(id)!.reject(new Error("Synthetic failed read")); }),
    };
}
type Scope = { queryAllByTestId: (id: string) => HTMLElement[] };
const lines = (dialog: Scope) => dialog.queryAllByTestId("calling-schedule-line").map((node) => node.textContent);
const spinners = (dialog: Scope) => dialog.queryAllByTestId("calling-schedule-reading").length;

test("one campaign from the list: loading shows the spinner, then the enforced hours in the saved values' place", async () => {
    const target = campaign("c1", "Alpha");
    const { dialog, reads, answer } = await open([target]);
    assert.deepEqual(reads, ["c1"], "GET /campaigns/{id} is read once when the window opens");
    assert.equal(spinners(dialog), 1, "the read is still running");
    assert.deepEqual(lines(dialog), [], "no hours are shown before the answer");
    assert.equal(dialog.queryByText(/Calling hours are checked in each contact's own timezone/) !== null, true, "the contact-timezone rule text stays");
    await answer("c1", { campaign: target, effective_schedule: enforced("Europe/London"), progress: null });
    assert.equal(spinners(dialog), 0);
    assert.deepEqual(lines(dialog), ["Calling schedule: Europe/London · 08:00–17:00 · Mon, Tue"]);
});

test("one campaign from the list: a failed read shows the saved values as before", async () => {
    const { dialog, fail } = await open([campaign("c1", "Alpha")]);
    assert.equal(spinners(dialog), 1);
    await fail("c1");
    assert.equal(spinners(dialog), 0);
    assert.deepEqual(lines(dialog), [SAVED_LINE]);
});

test("one campaign from the list: an answer with effective_schedule null, or without it, shows the saved values", async () => {
    const target = campaign("c1", "Alpha");
    const first = await open([target]);
    await first.answer("c1", { campaign: target, effective_schedule: null, progress: null });
    assert.deepEqual(lines(first.dialog), [SAVED_LINE]);
    cleanup();
    mock.restoreAll();
    const second = await open([target]);
    await second.answer("c1", { campaign: target });
    assert.deepEqual(lines(second.dialog), [SAVED_LINE]);
});

test("one campaign from the list: call anytime asked for and allowed shows the saved values; asked for but not allowed shows the enforced hours", async () => {
    const target = campaign("c1", "Alpha", "paused", { ...SAVED, ignore_schedule: true });
    const allowed = await open([target]);
    await allowed.answer("c1", { campaign: target, effective_schedule: enforced("Europe/London", { ignore_schedule_requested: true, ignore_schedule: true }) });
    assert.deepEqual(lines(allowed.dialog), [SAVED_LINE]);
    cleanup();
    mock.restoreAll();
    const refused = await open([target]);
    await refused.answer("c1", { campaign: target, effective_schedule: enforced("Europe/London", { ignore_schedule_requested: true, ignore_schedule_allowed: false, ignore_schedule: false }) });
    assert.deepEqual(lines(refused.dialog), ["Calling schedule: Europe/London · 08:00–17:00 · Mon, Tue"]);
});

test("two campaigns from the list: each is read and shows its own enforced hours, loaded or failed on its own", async () => {
    const alpha = campaign("c1", "Alpha");
    const beta = campaign("c2", "Beta");
    const { dialog, reads, answer, fail } = await open([alpha, beta]);
    assert.deepEqual([...reads].sort(), ["c1", "c2"]);
    assert.equal(spinners(dialog), 2, "both reads are still running");
    assert.deepEqual(lines(dialog), []);
    await answer("c2", { campaign: beta, effective_schedule: enforced("America/New_York"), progress: null });
    assert.equal(spinners(dialog), 1, "the other campaign is still being read");
    assert.deepEqual(lines(dialog), ["Calling schedule: America/New_York · 08:00–17:00 · Mon, Tue"]);
    await fail("c1");
    assert.equal(spinners(dialog), 0);
    assert.deepEqual(lines(dialog), [SAVED_LINE, "Calling schedule: America/New_York · 08:00–17:00 · Mon, Tue"], "the failed one shows its saved values, in list order");
});

test("two campaigns from the list: one that cannot start is not read", async () => {
    const { reads } = await open([campaign("c1", "Alpha"), campaign("c2", "Beta", "running")]);
    assert.deepEqual(reads, ["c1"]);
});

test("a host that brings the answer (the campaign page) is not read again, whether it brought hours or null", async () => {
    const withHours = await open([{ ...campaign("c1", "Alpha"), effective_schedule: enforced("Europe/London") }], false);
    assert.deepEqual(withHours.reads, []);
    assert.equal(spinners(withHours.dialog), 0);
    assert.deepEqual(lines(withHours.dialog), ["Calling schedule: Europe/London · 08:00–17:00 · Mon, Tue"]);
    cleanup();
    mock.restoreAll();
    const withNull = await open([{ ...campaign("c1", "Alpha"), effective_schedule: null }], false);
    assert.deepEqual(withNull.reads, []);
    assert.deepEqual(lines(withNull.dialog), [SAVED_LINE]);
});

test("the schedule read never holds the Start button back", async () => {
    const { dialog } = await open([campaign("c1", "Alpha")]);
    assert.equal(spinners(dialog), 1, "the schedule read is still running");
    const start = await dialog.findByRole("button", { name: "Start campaign" }) as HTMLButtonElement;
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 50)));
    assert.equal(start.disabled, false);
});
