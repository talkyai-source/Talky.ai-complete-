/**
 * FX6c Task 3 (section 1: "Nothing should be shown as available if it is not
 * actually usable."). While the account is suspended_subscription the server
 * refuses every campaign Start (403 tenant_suspended), so the shared Start
 * window, whichever entry point opened it, has Start disabled and shows the
 * server's own sentence.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { api } from "@/lib/api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import type { CampaignStartDialogState, CampaignStartTarget } from "./campaign-start-dialog";

let user: Record<string, unknown> | null = null;

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.endsWith("lib/auth-context")) {
        return { ...(originalLoad.call(this, id, ...args) as Record<string, unknown>), useSignedInUser: () => user };
    }
    return originalLoad.call(this, id, ...args);
};
let CampaignStartDialog: React.ComponentType<{ dialog: CampaignStartDialogState }>;
let useCampaignStartDialog: () => CampaignStartDialogState;
try {
    /* eslint-disable @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above */
    CampaignStartDialog = require("./campaign-start-dialog").CampaignStartDialog;
    useCampaignStartDialog = require("./campaign-start-dialog").useCampaignStartDialog;
    /* eslint-enable @typescript-eslint/no-require-imports */
} finally {
    loader._load = originalLoad;
}

const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); user = null; });

const SENTENCE = "This account is suspended, so campaigns cannot start. Billing and Support stay available.";
const SAVED = { timezone: "Asia/Karachi", time_window_start: "10:00", time_window_end: "22:00", allowed_days: [5, 6], ignore_schedule: false };
const campaign = (id: string, name: string) => ({ id, name, status: "paused", direction: "outbound", calling_config: SAVED, effective_schedule: null }) as unknown as Campaign & CampaignStartTarget;

function Host({ targets, listSkipped }: { targets: CampaignStartTarget[]; listSkipped: boolean }) {
    const dialog = useCampaignStartDialog();
    return <><button onClick={() => dialog.open(targets, { listSkipped })}>open</button><CampaignStartDialog dialog={dialog} /></>;
}

/** Opens the window with every check answering "ready" and plenty of minutes. */
async function open(targets: CampaignStartTarget[], listSkipped: boolean) {
    const starts: string[] = [];
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(dashboardApi, "getMinutesStatus", async () => ({ allocated: 100, used_minutes: 0, remaining_minutes: 100, unlimited: false, exhausted: false }));
    mock.method(dashboardApi, "startCampaign", async (id: string) => { starts.push(id); return {}; });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Host targets={targets} listSkipped={listSkipped} /></QueryClientProvider></AppRouterContext.Provider>);
    await act(async () => { screen.getByRole("button", { name: "open" }).click(); });
    const dialog = within(await screen.findByRole("dialog", { name: "Start campaign" }));
    const start = () => dialog.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement;
    return { dialog, start, starts };
}

for (const [entry, listSkipped, count] of [["a campaign's own Start button", false, 1], ["Resume Selected / the command bar", true, 2]] as const) {
    test(`suspended_subscription, opened from ${entry}: Start is disabled and the server's sentence shows`, async () => {
        user = { id: "u1", account_state: "suspended_subscription" };
        const targets = count === 1 ? [campaign("c1", "Alpha")] : [campaign("c1", "Alpha"), campaign("c2", "Beta")];
        const { dialog, start, starts } = await open(targets, listSkipped);
        const notice = dialog.getByTestId("campaign-start-account-suspended");
        assert.equal(notice.textContent, SENTENCE);
        assert.equal(notice.getAttribute("role"), "status");
        // Every other check has answered "ready"; Start stays off.
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
        assert.equal(start().disabled, true);
        await act(async () => { start().click(); });
        assert.deepEqual(starts, [], "no start request is sent");
        assert.equal((dialog.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled, false, "the window can still be closed");
    });
}

for (const [label, value] of [["active", { id: "u1", account_state: "active" }], ["no account_state", { id: "u1" }], ["a hold", { id: "u1", account_state: "suspended_hold" }], ["no signed-in user", null]] as const) {
    test(`${label}: the window is exactly as now (no sentence, Start offered once the checks answer)`, async () => {
        user = value;
        const { dialog, start } = await open([campaign("c1", "Alpha")], false);
        assert.equal(dialog.queryByTestId("campaign-start-account-suspended") === null, true);
        await waitFor(() => assert.equal(start().disabled, false));
    });
}
