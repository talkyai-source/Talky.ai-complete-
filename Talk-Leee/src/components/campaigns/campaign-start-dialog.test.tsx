import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { api } from "@/lib/api";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { CampaignStartDialog, useCampaignStartDialog } from "./campaign-start-dialog";

// Forms audit B-F21: the dialog is titled for what it does, a saved batch size
// of 0 (no limit on the server) reads "Unlimited", and the box has a name.
const request = api.request;
const getMinutesStatus = dashboardApi.getMinutesStatus;
const startCampaign = dashboardApi.startCampaign;
const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
afterEach(() => { cleanup(); clients.splice(0).forEach((c) => c.clear()); api.request = request; dashboardApi.getMinutesStatus = getMinutesStatus; dashboardApi.startCampaign = startCampaign; });

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

// Plan item: Resume must respect the saved first-speaker setting. The backend
// saves the choice in calling_config.first_speaker; the dialog opens on it and
// sends what it shows.
type StartCall = { id: string; body: Record<string, unknown> };

function HostFor({ campaigns }: { campaigns: Campaign[] }) {
    const dialog = useCampaignStartDialog();
    return <><button onClick={() => dialog.open(campaigns)}>open</button><CampaignStartDialog dialog={dialog} /></>;
}

function savedCampaign(status: string, calling_config: Record<string, unknown> | null, id = "c1"): Campaign {
    return { id, name: `Campaign ${id}`, status, direction: "outbound", calling_config } as unknown as Campaign;
}

async function openFor(campaigns: Campaign[]): Promise<StartCall[]> {
    const starts: StartCall[] = [];
    api.request = async <T,>() => ({ ready: true }) as T;
    dashboardApi.getMinutesStatus = async () => ({ allocated: 100, used_minutes: 0, remaining_minutes: 100, unlimited: false, exhausted: false });
    dashboardApi.startCampaign = async (id: string, body: unknown) => {
        starts.push({ id, body: { ...(body as Record<string, unknown>) } });
        return { message: "Started", jobs_enqueued: 1 };
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><HostFor campaigns={campaigns} /></QueryClientProvider></AppRouterContext.Provider>);
    await act(async () => { screen.getByRole("button", { name: "open" }).click(); });
    await screen.findByRole("dialog", { name: "Start campaign" });
    return starts;
}

function speaker(value: "agent" | "user"): HTMLInputElement {
    return document.querySelector(`input[name="first-speaker"][value="${value}"]`) as HTMLInputElement;
}

async function confirm(starts: StartCall[]) {
    const button = screen.getByRole("button", { name: "Start campaign" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false));
    await act(async () => { fireEvent.click(button); });
    await waitFor(() => assert.equal(starts.length > 0, true));
}

for (const status of ["draft", "paused", "stopped"]) {
    const label = status === "draft" ? "Start" : "Resume";
    test(`${label} (${status}): a campaign saved as callee-first opens on that choice and sends first_speaker "user"`, async () => {
        const starts = await openFor([savedCampaign(status, { first_speaker: "user", batch_size: 4 })]);
        assert.equal(speaker("user").checked, true);
        assert.equal(speaker("agent").checked, false);
        await confirm(starts);
        assert.equal(starts[0]!.id, "c1");
        assert.equal(starts[0]!.body.first_speaker, "user");
    });

    test(`${label} (${status}): a campaign saved as agent-first opens on agent and sends "agent"`, async () => {
        const starts = await openFor([savedCampaign(status, { first_speaker: "agent" })]);
        assert.equal(speaker("agent").checked, true);
        await confirm(starts);
        assert.equal(starts[0]!.body.first_speaker, "agent");
    });

    test(`${label} (${status}): nothing saved opens on agent and sends "agent"`, async () => {
        for (const config of [null, {}, { batch_size: 3 }, { first_speaker: "somebody" }]) {
            cleanup();
            const starts = await openFor([savedCampaign(status, config)]);
            assert.equal(speaker("agent").checked, true, JSON.stringify(config));
            assert.equal(speaker("user").checked, false, JSON.stringify(config));
            await confirm(starts);
            assert.equal(starts[0]!.body.first_speaker, "agent", JSON.stringify(config));
        }
    });

    test(`${label} (${status}): changing the choice sends the new value`, async () => {
        const starts = await openFor([savedCampaign(status, { first_speaker: "user" })]);
        await act(async () => { fireEvent.click(speaker("agent")); });
        assert.equal(speaker("agent").checked, true);
        await confirm(starts);
        assert.equal(starts[0]!.body.first_speaker, "agent");
    });
}

test("a choice saved for one campaign does not carry over to the next campaign that is opened", async () => {
    const campaigns = [savedCampaign("paused", { first_speaker: "user" }, "c1"), savedCampaign("paused", null, "c2")];
    function Two() {
        const dialog = useCampaignStartDialog();
        return <><button onClick={() => dialog.open([campaigns[0]!])}>open-1</button><button onClick={() => dialog.open([campaigns[1]!])}>open-2</button><button onClick={dialog.close}>close</button><CampaignStartDialog dialog={dialog} /></>;
    }
    api.request = async <T,>() => ({ ready: true }) as T;
    dashboardApi.getMinutesStatus = async () => ({ allocated: 100, used_minutes: 0, remaining_minutes: 100, unlimited: false, exhausted: false });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<AppRouterContext.Provider value={router}><QueryClientProvider client={client}><Two /></QueryClientProvider></AppRouterContext.Provider>);
    await act(async () => { screen.getByRole("button", { name: "open-1" }).click(); });
    assert.equal(speaker("user").checked, true);
    await act(async () => { screen.getByRole("button", { name: "close" }).click(); });
    await act(async () => { screen.getByRole("button", { name: "open-2" }).click(); });
    assert.equal(speaker("agent").checked, true);
});

test("a set of campaigns that all saved callee-first opens on that choice; mixed or unsaved sets open on agent", async () => {
    await openFor([savedCampaign("paused", { first_speaker: "user" }, "a"), savedCampaign("paused", { first_speaker: "user" }, "b")]);
    assert.equal(speaker("user").checked, true);
    cleanup();
    await openFor([savedCampaign("paused", { first_speaker: "user" }, "a"), savedCampaign("paused", { first_speaker: "agent" }, "b")]);
    assert.equal(speaker("agent").checked, true);
});
