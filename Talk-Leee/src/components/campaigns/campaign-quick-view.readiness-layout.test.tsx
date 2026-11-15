import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { api } from "@/lib/api";
import type { Campaign } from "@/lib/dashboard-api";
import { campaignActions } from "@/lib/permissions";
import { CampaignPerformanceTable } from "./campaign-performance-table";

// Live, 14 Nov 2026 (owner's screenshot of a paused campaign's quick view):
// with no phone line ready, the "No phone line is ready to dial out" warning
// sat inside the row of buttons. Resume floated above it and Duplicate, Edit,
// View Analytics and Delete were pushed out of line. The warning now has its
// own line under the buttons, and every button stays in the one row.
const NO_LINE = "No phone line is ready to dial out. Connect or switch on a line in Settings → Telephony.";
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const clients: QueryClient[] = [];
const originals = { request: api.request, frame: window.requestAnimationFrame, cancelFrame: window.cancelAnimationFrame, canvas: window.HTMLCanvasElement.prototype.getContext };
afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    api.request = originals.request;
    window.requestAnimationFrame = originals.frame;
    window.cancelAnimationFrame = originals.cancelFrame;
    window.HTMLCanvasElement.prototype.getContext = originals.canvas;
});

const PAUSED = {
    id: "c-1", name: "Estimation", status: "paused", direction: "outbound", system_prompt: "", voice_id: "v",
    max_concurrent_calls: 1, total_leads: 1, calls_completed: 1, calls_failed: 0, created_at: "2026-11-01T00:00:00Z",
} as Campaign;
const MANAGER = campaignActions(["campaigns:create", "campaigns:read", "campaigns:update", "campaigns:delete"]);

function openQuickView(readiness: { ready: boolean; reason: string | null }) {
    const asked: string[] = [];
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    window.HTMLCanvasElement.prototype.getContext = (() => null) as typeof originals.canvas;
    api.request = (async (options: { path: string }) => {
        asked.push(options.path);
        if (options.path.endsWith("/readiness")) return { campaign_id: "c-1", reason_code: readiness.ready ? "ready" : "no_ready_trunk", caller_id: null, trunk_id: null, ...readiness };
        throw new Error(`unexpected request ${options.path}`);
    }) as typeof api.request;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>
                <CampaignPerformanceTable campaigns={[PAUSED]} loading={false} error=""
                    onPause={async () => {}} onResume={async () => {}} onDelete={async () => {}} onDuplicate={async () => {}}
                    onStop={async () => {}} onPauseSelected={async () => {}} allowed={MANAGER} />
            </QueryClientProvider>
        </AppRouterContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Estimation/ }));
    return { view: within(screen.getByRole("dialog")), asked };
}

test("with no line ready, the warning is under the buttons and every button stays in one row", async () => {
    const { view, asked } = openQuickView({ ready: false, reason: NO_LINE });
    const notice = (await view.findByText(/No phone line is ready to dial out/, undefined, { timeout: 5000 })).closest("[role='status']") as HTMLElement;
    assert.ok(notice, "the warning is a status message");

    const resume = view.getByRole("button", { name: "Resume" }) as HTMLButtonElement;
    assert.equal(resume.disabled, true);
    const row = view.getByRole("link", { name: "Open page" }).parentElement!;
    for (const name of ["Resume", "Duplicate", "Edit", "View Analytics", "Delete"]) {
        assert.ok(row.contains(view.getByRole("button", { name })), `${name} is in the button row`);
    }
    assert.equal(row.contains(notice), false, "the warning is not inside the button row");
    assert.equal(Boolean(row.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING), true, "the warning comes after the row");
    assert.equal(notice.className.includes("max-w-sm"), false, "the warning spans the window like the notes under it");
    assert.ok(view.getByRole("button", { name: "Check again" }), "the warning keeps its Check again");
    assert.equal(view.getAllByText(/No phone line is ready to dial out/).length, 1, "the warning is shown once");
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(asked.filter((path) => path.endsWith("/readiness")).length, 1, "readiness is read once for the button and the warning");
});

test("with a line ready, there is no warning and Resume can be pressed", async () => {
    const { view } = openQuickView({ ready: true, reason: null });
    const resume = view.getByRole("button", { name: "Resume" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(resume.disabled, false), { timeout: 5000 });
    assert.equal(view.queryByText(/No phone line is ready to dial out/), null);
});
