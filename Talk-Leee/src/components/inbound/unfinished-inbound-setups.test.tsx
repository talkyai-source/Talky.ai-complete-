/**
 * Live test, 14 Nov 2026: an inbound campaign whose step 1 was saved and whose
 * step 2 could not be finished was in neither campaign list, and its own
 * address said "not found". The Inbound Campaigns page now lists such
 * campaigns with a link straight to step 2.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { UnfinishedInboundSetups } from "@/components/inbound/unfinished-inbound-setups";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { inboundApi, type InboundCampaign } from "@/lib/inbound-api";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
const originals = { campaigns: dashboardApi.listCampaigns, setups: inboundApi.list };
let client: QueryClient | undefined;
afterEach(() => {
    cleanup(); client?.clear();
    dashboardApi.listCampaigns = originals.campaigns; inboundApi.list = originals.setups;
});

const campaign = (over: Partial<Campaign>) => ({ id: "c1", name: "Front desk", status: "draft", direction: "inbound", ...over }) as Campaign;
const setup = (campaignId: string, status = "active") => ({ id: `setup-${campaignId}`, campaign_id: campaignId, status }) as InboundCampaign;

function mount(campaigns: Campaign[] | Error, setups: InboundCampaign[] | Error) {
    const asked: Array<boolean | undefined> = [];
    dashboardApi.listCampaigns = async () => { if (campaigns instanceof Error) throw campaigns; return { campaigns }; };
    inboundApi.list = (async (options: { includeArchived?: boolean } = {}) => { asked.push(options.includeArchived); if (setups instanceof Error) throw setups; return setups; }) as typeof inboundApi.list;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={client}><UnfinishedInboundSetups /></QueryClientProvider>);
    return asked;
}

test("an inbound campaign with no number setup is listed with a link to its step 2", async () => {
    const asked = mount(
        [
            campaign({ id: "left behind", name: "Front desk" }),
            campaign({ id: "c2", name: "Has a number" }),
            campaign({ id: "c3", name: "Setup was archived" }),
            campaign({ id: "c4", name: "Outbound draft", direction: "outbound" }),
            campaign({ id: "c5", name: "Deleted one", status: "deleted" }),
        ],
        [setup("c2"), setup("c3", "archived")],
    );
    const link = await screen.findByRole("link", { name: "Continue setup of Front desk" }, { timeout: 5000 });
    assert.equal(link.getAttribute("href"), "/inbound-campaigns/new?campaign_id=left%20behind");
    assert.equal(screen.getAllByRole("link").length, 1, "only the campaign with no number setup is listed");
    assert.ok(screen.getByRole("heading", { name: "Setup not finished" }));
    assert.match(document.body.textContent ?? "", /no phone number yet, so it cannot take calls and is not in the list below/);
    assert.deepEqual(asked, [true], "archived number setups are read too, so an archived setup does not look unfinished");
});

test("with every inbound campaign set up it shows nothing", async () => {
    const asked = mount([campaign({ id: "c2" })], [setup("c2")]);
    await waitFor(() => assert.equal(asked.length, 1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(document.body.textContent, "");
});

test("when a read fails it shows nothing rather than a guess", async () => {
    const asked = mount([campaign({ id: "left-behind" })], new Error("unavailable"));
    await waitFor(() => assert.equal(asked.length, 1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(document.body.textContent, "");
});
