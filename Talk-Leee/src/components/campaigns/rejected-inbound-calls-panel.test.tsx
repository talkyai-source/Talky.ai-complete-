import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { RejectedInboundCallsPanel } from "@/components/campaigns/rejected-inbound-calls-panel";
import { api } from "@/lib/api";
import type { RejectedInboundCallItem } from "@/lib/api";

const originalListRejectedInboundCalls = api.listRejectedInboundCalls;

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

function rejection(id: string, reason: string): RejectedInboundCallItem {
    return {
        id,
        source: "pre_row" as const,
        occurred_at: "2026-09-03T00:00:00Z",
        status: "denied",
        reason,
        caller_ani: "+15550001111",
        called_did: "+15550002222",
    };
}

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

test("a superseded campaign request cannot replace the current rejection feed", async () => {
    const first = deferred<Awaited<ReturnType<typeof api.listRejectedInboundCalls>>>();
    api.listRejectedInboundCalls = async (input) => {
        if (input?.campaignId === "campaign-a") return first.promise;
        return {
            items: [rejection("current", "tenant_conflict")],
            page: 1,
            page_size: 25,
            total: 1,
            server_time: "2026-09-03T00:00:02Z",
        };
    };

    const view = render(<RejectedInboundCallsPanel campaignId="campaign-a" />);
    await waitFor(() => assert.equal(screen.queryByText("Number routing conflict"), null));
    view.rerender(<RejectedInboundCallsPanel campaignId="campaign-b" />);
    assert.ok(await screen.findByText("Number routing conflict"));

    await act(async () => {
        first.resolve({
            items: [rejection("superseded", "unknown_did")],
            page: 1,
            page_size: 25,
            total: 1,
            server_time: "2026-09-03T00:00:01Z",
        });
        await first.promise;
    });

    await waitFor(() => {
        assert.ok(screen.getByText("Number routing conflict"));
        assert.equal(screen.queryByText("Unknown number"), null);
    });
});

test("switching campaigns immediately removes an already-loaded rejection feed", async () => {
    const second = deferred<Awaited<ReturnType<typeof api.listRejectedInboundCalls>>>();
    api.listRejectedInboundCalls = async (input) => (
        input?.campaignId === "campaign-a"
            ? {
                items: [rejection("previous", "unknown_did")],
                page: 1,
                page_size: 25,
                total: 1,
                server_time: "2026-09-03T00:00:01Z",
            }
            : second.promise
    );

    const view = render(<RejectedInboundCallsPanel campaignId="campaign-a" />);
    assert.ok(await screen.findByText("Unknown number"));

    view.rerender(<RejectedInboundCallsPanel campaignId="campaign-b" />);

    await waitFor(() => assert.equal(screen.queryByText("Unknown number"), null));
    assert.ok(screen.getByText("Loading rejected calls…"));

    await act(async () => {
        second.resolve({
            items: [rejection("current", "tenant_conflict")],
            page: 1,
            page_size: 25,
            total: 1,
            server_time: "2026-09-03T00:00:02Z",
        });
        await second.promise;
    });
    assert.ok(await screen.findByText("Number routing conflict"));
});
