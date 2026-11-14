/**
 * Live test, 14 Nov 2026: with no verified phone number in the account, "New
 * inbound campaign" let the user finish the whole of step 1 and only then
 * showed a step 2 that could not be completed. Step 1 now says so first.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { InboundSetupNeedsNotice } from "@/components/inbound/inbound-setup-needs-notice";
import { api } from "@/lib/api";
import { inboundApi } from "@/lib/inbound-api";
import { inboundSetupNeeds } from "@/lib/inbound-setup-needs";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
const originals = { request: api.request, numbers: inboundApi.availablePhoneNumbers };
let client: QueryClient | undefined;
afterEach(() => {
    cleanup(); client?.clear();
    api.request = originals.request; inboundApi.availablePhoneNumbers = originals.numbers;
});

const READY_TRUNK = { id: "t1", trunk_name: "Main", direction: "both", is_active: true, inbound_runtime_ready: true };
const DOWN_TRUNK = { ...READY_TRUNK, id: "t2", inbound_runtime_ready: false };
const NUMBER = { id: "n1", e164: "+442000000001", masked_number: "+44 •••• ••0001", verification_status: "verified", assignment_status: "available", version: 1 };

function mount({ numbers, trunks, canAssignNumber = true }: { numbers: unknown[]; trunks: unknown[]; canAssignNumber?: boolean }) {
    let reads = 0;
    inboundApi.availablePhoneNumbers = (async () => { reads += 1; return numbers; }) as typeof inboundApi.availablePhoneNumbers;
    api.request = async <T,>() => { reads += 1; return trunks as T; };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={client}><InboundSetupNeedsNotice canAssignNumber={canAssignNumber} /></QueryClientProvider>);
    return () => reads;
}

test("with no verified number and no connected trunk, step 1 says so before any work is done", async () => {
    mount({ numbers: [], trunks: [DOWN_TRUNK] });
    const note = await screen.findByRole("status", undefined, { timeout: 5000 });
    const text = note.textContent ?? "";
    assert.match(text, /This campaign will not be able to take calls yet\./);
    assert.match(text, /Still needed for step 2: a verified phone number \(none is available: ask an administrator to verify one\); an inbound trunk that is connected right now\./);
    assert.match(text, /kept under .Setup not finished. on the Inbound Campaigns page/);
});

test("with a number but no connected trunk it names only the trunk", async () => {
    mount({ numbers: [NUMBER], trunks: [DOWN_TRUNK] });
    const text = (await screen.findByRole("status", undefined, { timeout: 5000 })).textContent ?? "";
    assert.match(text, /an inbound trunk that is connected right now/);
    assert.doesNotMatch(text, /verified phone number/);
});

test("with a number and a connected trunk it shows nothing", async () => {
    const reads = mount({ numbers: [NUMBER], trunks: [READY_TRUNK] });
    await waitFor(() => assert.equal(reads(), 2));
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(document.body.textContent, "");
});

test("the wording is the same list step 2 shows next to its button", () => {
    assert.deepEqual(inboundSetupNeeds({ needsCampaign: false, numbers: 2, trunks: 1 }), []);
    assert.deepEqual(inboundSetupNeeds({ needsCampaign: true, numbers: 0, trunks: 0 }), [
        "an inbound AI campaign to attach",
        "a verified phone number (none is available: ask an administrator to verify one)",
        "an inbound trunk that is connected right now",
    ]);
});
