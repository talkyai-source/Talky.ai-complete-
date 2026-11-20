import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { Call } from "@/lib/dashboard-api";
import { DialAgainButton } from "./dial-again-button";

afterEach(() => cleanup());
function mount(overrides: Partial<Call>) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const call = { id: "c1", campaign_id: "camp-1", lead_id: "", phone_number: "+14165550123", status: "completed", direction: "outbound", ...overrides } as Call;
    render(<QueryClientProvider client={client}><DialAgainButton call={call} /></QueryClientProvider>);
}

test("history list items have no lead_id yet Dial again still shows", () => {
    mount({});
    assert.equal(screen.queryByText("Dial again") !== null, true);
});

test("inbound or campaign-less calls still show no Dial again", () => {
    mount({ direction: "inbound" });
    assert.equal(screen.queryByText("Dial again"), null);
    cleanup();
    mount({ campaign_id: "" as unknown as string });
    assert.equal(screen.queryByText("Dial again"), null);
});
