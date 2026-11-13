import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { alertsQueryKeys } from "@/lib/alerts-api";
import { CampaignPerformanceTable } from "./campaign-performance-table";
import { AlertTimeline } from "./alert-timeline";
import { notificationsStore } from "@/lib/notifications";

const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const originalFetch = globalThis.fetch;
const originalFrame = window.requestAnimationFrame;
const originalCancelFrame = window.cancelAnimationFrame;
const originalCanvasContext = window.HTMLCanvasElement.prototype.getContext;
afterEach(() => {
    cleanup(); clients.splice(0).forEach((client) => client.clear());
    notificationsStore.setIdentity(null);
    globalThis.fetch = originalFetch; window.localStorage.clear();
    window.requestAnimationFrame = originalFrame;
    window.cancelAnimationFrame = originalCancelFrame;
    window.HTMLCanvasElement.prototype.getContext = originalCanvasContext;
});

function mount(children: React.ReactNode, seed?: (client: QueryClient) => void) {
    notificationsStore.setIdentity({ tenantId: "synthetic-tenant", userId: "synthetic-user" });
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    // JSDOM has no canvas renderer; the select supports a null measurement context.
    window.HTMLCanvasElement.prototype.getContext = (() => null) as typeof originalCanvasContext;
    globalThis.fetch = async () => { throw new Error("No network is allowed in unavailable-control tests"); };
    const client = new QueryClient({ defaultOptions: { queries: {
        enabled: false, retry: false, staleTime: Infinity, gcTime: Infinity,
    } } });
    clients.push(client); seed?.(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}>
        {children}
    </QueryClientProvider></AppRouterContext.Provider>);
}

test("the working export dialog does not offer a browser-only recurring report", async () => {
    window.localStorage.setItem("campaigns.performance.reportSchedule", JSON.stringify({ enabled: true }));
    mount(<CampaignPerformanceTable campaigns={[]} loading={false} error=""
        onPause={async () => {}} onResume={async () => {}} onDelete={async () => {}}
        onDuplicate={async () => {}} onStop={async () => {}} onPauseSelected={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    const dialog = within(await screen.findByRole("dialog"));
    assert.ok(dialog.getByRole("button", { name: "CSV" }));
    assert.ok(dialog.queryByText("Scheduling", { exact: true }) === null,
        "A recurring schedule with no server execution must not be offered");
    assert.ok(dialog.queryByText(/prototype mode/i) === null,
        "The export dialog must not include a prototype-only schedule");
});

test("real alert details retain acknowledgement but do not offer a local-only rule builder", async () => {
    mount(<AlertTimeline campaigns={[]} />, (client) => {
        const actor = notificationsStore.getSnapshot();
        client.setQueryData(alertsQueryKeys.list({ severity: [], type: [], status: [] }, actor.scopeKey, actor.generation), [{
            id: "synthetic-alert", title: "Synthetic saved alert", description: "Saved server event",
            severity: "Warning", type: "Campaign", status: "Active", acknowledged: false,
            createdAt: "2026-11-06T12:00:00Z", updatedAt: "2026-11-06T12:00:00Z", relatedCampaignIds: [],
        }]);
    });
    fireEvent.click((await screen.findByText("Synthetic saved alert")).closest("button")!);
    const dialog = within(await screen.findByRole("dialog"));
    assert.ok(dialog.getByRole("button", { name: "Acknowledge" }));
    assert.ok(dialog.queryByRole("button", { name: "Create Rule" }) === null,
        "A rule with no server execution must not be offered");
    assert.ok(screen.queryByText("Rule builder", { exact: true }) === null,
        "The unavailable rule builder must remain hidden");
});
