import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";

import { RejectedInboundCallsPanel } from "@/components/campaigns/rejected-inbound-calls-panel";
import { api } from "@/lib/api";

const originalListRejectedInboundCalls = api.listRejectedInboundCalls;

afterEach(() => {
    cleanup();
    api.listRejectedInboundCalls = originalListRejectedInboundCalls;
});

test("shows durable denials and after-hours calls without exposing private ANI", async () => {
    let campaignId: string | undefined;
    api.listRejectedInboundCalls = async (input) => {
        campaignId = input?.campaignId;
        return {
            items: [
                {
                    id: "rejection-1",
                    source: "pre_row",
                    occurred_at: "2026-08-31T10:00:00Z",
                    status: "denied",
                    reason: "unknown_did",
                    caller_ani: null,
                    called_did: "+15550001111",
                },
                {
                    id: "call-1",
                    source: "call",
                    occurred_at: "2026-08-31T09:00:00Z",
                    status: "after_hours",
                    reason: "after_hours_closed",
                    caller_ani: "+15550002222",
                    called_did: "+15550003333",
                },
            ],
            page: 1,
            page_size: 25,
            total: 2,
            server_time: "2026-08-31T10:00:01Z",
        };
    };

    render(<RejectedInboundCallsPanel campaignId="campaign-1" />);

    assert.ok(await screen.findByText("Unknown number"));
    assert.ok(screen.getByText("After hours"));
    assert.ok(screen.getByText("Private / unavailable"));
    assert.ok(screen.getByText("+15550002222"));
    assert.equal(campaignId, "campaign-1");
});

test("shows a healthy empty state", async () => {
    api.listRejectedInboundCalls = async () => ({
        items: [],
        page: 1,
        page_size: 25,
        total: 0,
        server_time: "2026-08-31T10:00:01Z",
    });

    render(<RejectedInboundCallsPanel />);

    assert.ok(await screen.findByText("No rejected inbound calls"));
});
