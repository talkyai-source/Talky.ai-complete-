import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import type { Campaign } from "@/lib/dashboard-api";
import { CampaignReadinessNotice, CampaignStartControl, useCampaignReadiness } from "./campaign-readiness";
import { CommandBar } from "./command-bar";
import { CampaignPerformanceTable } from "./campaign-performance-table";

const request = api.request;
const requestAnimationFrame = window.requestAnimationFrame;
const cancelAnimationFrame = window.cancelAnimationFrame;
const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); api.request = request; window.requestAnimationFrame = requestAnimationFrame; window.cancelAnimationFrame = cancelAnimationFrame; localStorage.clear(); });

function mount(children: React.ReactNode) {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}>{children}</QueryClientProvider></AppRouterContext.Provider>);
    return client;
}

test("start is disabled until the server proves readiness, including cloud routes", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    const requests: string[] = [];
    let starts = 0;
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => { requests.push(options.path); assert.equal(options.method, undefined); return await pending as T; };
    mount(<CampaignStartControl campaignIds={["campaign-a"]} onStart={async () => { starts++; }} label="Start" />);
    const button = screen.getByRole("button", { name: "Start" });
    assert.equal((button as HTMLButtonElement).disabled, true);
    fireEvent.click(button);
    assert.equal(starts, 0);
    await act(async () => resolve({ ready: true, reason_code: "sip_not_required", caller_id: null, trunk_id: null }));
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
    fireEvent.click(button);
    await waitFor(() => assert.equal(starts, 1));
    assert.deepEqual(requests, ["/campaigns/campaign-a/readiness"]);
});

test("a failed refresh disables previously ready start and offers a successful retry", async () => {
    let failing = false;
    api.request = async <T,>() => { if (failing) throw new Error("Unavailable"); return { ready: true } as T; };
    const client = mount(<CampaignStartControl campaignIds={["campaign-a"]} onStart={async () => {}} label="Resume" />);
    const button = screen.getByRole("button", { name: "Resume" });
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
    failing = true;
    await act(async () => { await client.invalidateQueries({ queryKey: ["campaign-readiness"] }); });
    await screen.findByText(/Calling readiness could not be checked/);
    assert.equal((button as HTMLButtonElement).disabled, true);
    failing = false;
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
});

test("bulk resume waits for every selected route and shows the server reason", async () => {
    let starts = 0;
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => ({ ready: options.path.includes("ready-campaign"), reason: "The selected trunk has stale live health." }) as T;
    mount(<CampaignStartControl campaignIds={["ready-campaign", "blocked-campaign"]} onStart={async () => { starts++; }} label="Resume Selected" />);
    await screen.findByText(/The selected trunk has stale live health/);
    const button = screen.getByRole("button", { name: "Resume Selected" });
    assert.equal((button as HTMLButtonElement).disabled, true);
    fireEvent.click(button);
    assert.equal(starts, 0);
});

test("a closed or nonlaunch surface performs no readiness request", () => {
    let requests = 0;
    api.request = async <T,>() => { requests++; return { ready: true } as T; };
    function Hidden() { const readiness = useCampaignReadiness(["campaign-a"], false); return <CampaignReadinessNotice readiness={readiness} />; }
    mount(<Hidden />);
    assert.equal(requests, 0);
});

const campaign = { id: "campaign-a", name: "Example campaign", status: "paused", direction: "outbound", created_at: "2026-10-02T00:00:00Z", total_leads: 1, calls_completed: 0, calls_failed: 0 } as Campaign;

test("table selection and details block resume while the route is unavailable", async () => {
    api.request = async <T,>() => ({ ready: false, reason: "Selected trunk unavailable" }) as T;
    let resumed = 0;
    mount(<CampaignPerformanceTable campaigns={[campaign]} loading={false} error="" onPause={async () => {}} onResume={async () => { resumed++; }} onDelete={async () => {}} onDuplicate={async () => {}} />);
    // Finish the table's initial preference hydration and selection-reset frames.
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    fireEvent.click(screen.getByRole("checkbox", { name: `Select ${campaign.name}` }));
    await screen.findByText(/Selected trunk unavailable/);
    const button = screen.getByRole("button", { name: "Resume Selected" });
    assert.equal((button as HTMLButtonElement).disabled, true);
    fireEvent.click(button);
    assert.equal(resumed, 0);
    fireEvent.click(screen.getByRole("button", { name: new RegExp(campaign.name) }));
    const dialog = within(screen.getByRole("dialog"));
    await dialog.findByText(/Selected trunk unavailable/);
    const detailResume = dialog.getByRole("button", { name: "Resume" });
    assert.equal((detailResume as HTMLButtonElement).disabled, true);
    fireEvent.click(detailResume);
    assert.equal(resumed, 0);
});

test("command-bar keyboard execution cannot bypass unavailable resume routes", async () => {
    api.request = async <T,>() => ({ ready: false, reason: "Selected trunk unavailable" }) as T;
    let resumed = 0;
    mount(<CommandBar campaigns={[campaign]} onPause={async () => {}} onResume={async () => { resumed++; }} />);
    fireEvent.click(screen.getByRole("button", { name: "Ctrl + K" }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "/resume" } });
    await screen.findAllByText(/Selected trunk unavailable/);
    const action = screen.getByRole("button", { name: /Resume all paused campaigns/ });
    assert.equal((action as HTMLButtonElement).disabled, true);
    fireEvent.keyDown(input, { key: "Enter" });
    assert.equal(resumed, 0);
    assert.ok(screen.getByRole("dialog"));
});
