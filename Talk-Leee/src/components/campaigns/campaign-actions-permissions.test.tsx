import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import type { Campaign } from "@/lib/dashboard-api";
import { campaignActions } from "@/lib/permissions";
import { CampaignPerformanceTable } from "./campaign-performance-table";
import { CommandBar } from "./command-bar";

// QA F1-21 (QA-UI-041): a read-only user was offered New Campaign, bulk
// Pause/Resume/Delete and the quick view's Duplicate/Edit/Delete, all of
// which the server refuses for its role. Campaign actions now follow the
// effective permissions; reading, opening and exporting stay available.
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];
const originalFetch = globalThis.fetch;
const originalFrame = window.requestAnimationFrame;
const originalCancelFrame = window.cancelAnimationFrame;
const originalCanvasContext = window.HTMLCanvasElement.prototype.getContext;
afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    globalThis.fetch = originalFetch;
    window.requestAnimationFrame = originalFrame;
    window.cancelAnimationFrame = originalCancelFrame;
    window.HTMLCanvasElement.prototype.getContext = originalCanvasContext;
});

const CAMPAIGN = {
    id: "c-1", name: "Spring push", status: "paused", direction: "outbound", system_prompt: "", voice_id: "v",
    max_concurrent_calls: 1, total_leads: 3, calls_completed: 1, calls_failed: 0, created_at: "2026-11-01T00:00:00Z",
} as Campaign;
const READ_ONLY = campaignActions(["campaigns:read", "calls:read"]);
const MANAGER = campaignActions(["campaigns:create", "campaigns:read", "campaigns:update", "campaigns:delete"]);

function mount(children: React.ReactNode) {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    window.HTMLCanvasElement.prototype.getContext = (() => null) as typeof originalCanvasContext;
    globalThis.fetch = async () => { throw new Error("no network in this test"); };
    const client = new QueryClient({ defaultOptions: { queries: { enabled: false, retry: false, staleTime: Infinity, gcTime: Infinity } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}>{children}</QueryClientProvider></AppRouterContext.Provider>);
}
function mountTable(allowed: ReturnType<typeof campaignActions>) {
    mount(<CampaignPerformanceTable campaigns={[CAMPAIGN]} loading={false} error=""
        onPause={async () => {}} onResume={async () => {}} onDelete={async () => {}} onDuplicate={async () => {}}
        onStop={async () => {}} onPauseSelected={async () => {}} allowed={allowed} />);
}
function openQuickView() {
    fireEvent.click(screen.getByRole("button", { name: /Spring push/ }));
    return within(screen.getByRole("dialog"));
}

test("a read-only role is offered reading and export, not campaign changes", () => {
    mountTable(READ_ONLY);
    assert.equal(screen.queryByRole("link", { name: "Create campaign" }) === null, true, "New Campaign offered");
    assert.ok(screen.getByRole("button", { name: "Export" }));

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Spring push" }));
    assert.ok(screen.getByText("1 selected"));
    assert.ok(screen.getByRole("button", { name: /Export Selected/ }));
    for (const name of [/Pause Selected/, /Resume Selected/, /Delete Selected/]) {
        assert.equal(screen.queryByRole("button", { name }) === null, true, `${name} offered`);
    }

    const quickView = openQuickView();
    assert.ok(quickView.getByRole("link", { name: "Open page" }));
    assert.ok(quickView.getByRole("button", { name: "View Analytics" }));
    for (const name of ["Resume", "Pause", "Duplicate", "Edit", "Delete"]) {
        assert.equal(quickView.queryByRole("button", { name }) === null, true, `${name} offered`);
    }
});

test("a role that manages campaigns keeps every action", () => {
    mountTable(MANAGER);
    assert.ok(screen.getByRole("link", { name: "Create campaign" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Spring push" }));
    for (const name of [/Pause Selected/, /Resume Selected/, /Delete Selected/, /Export Selected/]) {
        assert.ok(screen.getByRole("button", { name }), `${name} missing`);
    }
    const quickView = openQuickView();
    for (const name of ["Resume", "Duplicate", "Edit", "Delete"]) {
        assert.ok(quickView.getByRole("button", { name }), `${name} missing`);
    }
});

function commandActions(allowed: ReturnType<typeof campaignActions>) {
    mount(<CommandBar campaigns={[CAMPAIGN]} onPause={async () => {}} onResume={() => {}} onExport={() => {}} allowed={allowed} />);
    fireEvent.click(screen.getByRole("button", { name: "Ctrl + K" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "/" } });
    return document.body.textContent ?? "";
}

test("the command bar offers a read-only role no campaign changes", () => {
    const text = commandActions(READ_ONLY);
    assert.match(text, /Export campaigns/);
    for (const action of ["Create new campaign", "Pause all active campaigns", "Resume all paused campaigns"]) {
        assert.equal(text.includes(action), false, `${action} offered`);
    }
});

test("the command bar keeps campaign actions for a role that manages campaigns", () => {
    const text = commandActions(MANAGER);
    for (const action of ["Export campaigns", "Create new campaign", "Pause all active campaigns", "Resume all paused campaigns"]) {
        assert.ok(text.includes(action), `${action} missing`);
    }
});

test("while permissions are unknown, every campaign action stays offered", () => {
    const unknown = campaignActions(undefined);
    assert.deepEqual(unknown, { create: true, update: true, delete: true });
});
