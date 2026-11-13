import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { api } from "@/lib/api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { CampaignStartDialog, useCampaignStartDialog } from "./campaign-start-dialog";

// Forms audit B-F21: the dialog is titled for what it does, a saved batch size
// of 0 (no limit on the server) reads "Unlimited", and the box has a name.
const request = api.request;
const getMinutesStatus = dashboardApi.getMinutesStatus;
const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
afterEach(() => { cleanup(); clients.splice(0).forEach((c) => c.clear()); api.request = request; dashboardApi.getMinutesStatus = getMinutesStatus; });

function Host({ batch }: { batch: number }) {
    const dialog = useCampaignStartDialog();
    const campaign = { id: "c1", name: "Example", status: "paused", direction: "outbound", calling_config: { batch_size: batch } } as unknown as Campaign;
    return <><button onClick={() => dialog.open([campaign])}>open</button><CampaignStartDialog dialog={dialog} /></>;
}

async function open(batch: number) {
    api.request = async <T,>() => ({ ready: true }) as T;
    dashboardApi.getMinutesStatus = async () => ({ allocated: 100, used_minutes: 0, remaining_minutes: 100, unlimited: false, exhausted: false });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Host batch={batch} /></QueryClientProvider></AppRouterContext.Provider>);
    await act(async () => { screen.getByRole("button", { name: "open" }).click(); });
    return screen.findByRole("dialog", { name: "Start campaign" });
}

test("a saved batch size of 0 is shown as Unlimited and the box is named", async () => {
    await open(0);
    const box = screen.getByLabelText("Calls at a time") as HTMLInputElement;
    assert.equal(box.value, "");
    assert.equal(box.placeholder, "Unlimited");
});

test("a saved batch size of 5 is shown as 5", async () => {
    await open(5);
    assert.equal((screen.getByLabelText("Calls at a time") as HTMLInputElement).value, "5");
});
