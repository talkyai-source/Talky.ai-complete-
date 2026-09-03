import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { CallIssuesPanel } from "@/components/campaigns/call-issues-panel";
import { api, sharedHttpClient } from "@/lib/api";
import type { CallIssue } from "@/lib/api";
import type { HttpRequestOptions } from "@/lib/http-client";

const originalListCallIssues = api.listCallIssues;
const httpClient = sharedHttpClient();
const originalRequest = httpClient.request;

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

function inboundIssue(campaignId: string, title: string): CallIssue {
    return {
        job_id: `issue-${campaignId}`,
        phone_number: `phone-${campaignId}`,
        campaign_id: campaignId,
        status: "failed",
        reason_code: `reason-${campaignId}`,
        category: "inbound_processing",
        title,
        suggestion: "Inspect the durable failure.",
        severity: "error",
        stage: "processing",
        attempts: 1,
        updated_at: "2026-09-03T00:00:00Z",
    };
}

afterEach(() => {
    cleanup();
    api.listCallIssues = originalListCallIssues;
    httpClient.request = originalRequest;
});

test("the API client serializes inbound direction on the issues request", async () => {
    const requests: HttpRequestOptions[] = [];
    httpClient.request = async <TResponse = unknown, TBody = unknown>(
        options: HttpRequestOptions<TBody>,
    ): Promise<TResponse> => {
        requests.push(options);
        return { items: [], server_time: "2026-09-03T00:00:01Z" } as TResponse;
    };

    await api.listCallIssues({
        campaignId: "campaign-inbound",
        windowMinutes: 30,
        direction: "inbound",
    });

    assert.equal(requests[0]?.path, "/calls/issues");
    assert.deepEqual(requests[0]?.query, {
        campaign_id: "campaign-inbound",
        window_minutes: 30,
        direction: "inbound",
    });

    await api.listCallIssues({ campaignId: "campaign-outbound" });
    assert.deepEqual(requests[1]?.query, { campaign_id: "campaign-outbound" });
});

test("an inbound issues panel requests the inbound durable issue feed", async () => {
    let received: Parameters<typeof api.listCallIssues>[0];
    api.listCallIssues = async (input) => {
        received = input;
        return {
            items: [
                {
                    job_id: "inbound-failure-1",
                    phone_number: "+15550001111",
                    campaign_id: "campaign-inbound",
                    status: "failed",
                    reason_code: "inbound_processing_failed",
                    category: "inbound_processing",
                    title: "Inbound call processing failed",
                    suggestion: "Open the call record and inspect the persisted failure.",
                    severity: "error",
                    stage: "processing",
                    attempts: 1,
                    updated_at: "2026-09-03T00:00:00Z",
                },
            ],
            server_time: "2026-09-03T00:00:01Z",
        };
    };

    render(
        <CallIssuesPanel
            campaignId="campaign-inbound"
            direction="inbound"
            title="Inbound call issues"
        />,
    );

    assert.ok(await screen.findByText("Inbound call issues"));
    await waitFor(() => assert.equal(received?.campaignId, "campaign-inbound"));
    assert.equal(received?.direction, "inbound");
    assert.ok(screen.getByText(/1 call$/));
});

test("a superseded campaign poll cannot replace the current issue feed", async () => {
    const first = deferred<Awaited<ReturnType<typeof api.listCallIssues>>>();
    const requested: Array<string | undefined> = [];
    api.listCallIssues = async (input) => {
        requested.push(input?.campaignId);
        if (input?.campaignId === "campaign-a") return first.promise;
        return {
            items: [inboundIssue("campaign-b", "Current campaign failure")],
            server_time: "2026-09-03T00:00:02Z",
        };
    };

    const view = render(
        <CallIssuesPanel campaignId="campaign-a" direction="inbound" />,
    );
    await waitFor(() => assert.deepEqual(requested, ["campaign-a"]));

    view.rerender(
        <CallIssuesPanel campaignId="campaign-b" direction="inbound" />,
    );
    assert.ok(await screen.findByText(/Current campaign failure/));

    await act(async () => {
        first.resolve({
            items: [inboundIssue("campaign-a", "Superseded campaign failure")],
            server_time: "2026-09-03T00:00:01Z",
        });
        await first.promise;
    });

    await waitFor(() => {
        assert.ok(screen.getByText(/Current campaign failure/));
        assert.equal(screen.queryByText(/Superseded campaign failure/), null);
    });
});

test("switching campaigns immediately removes an already-loaded issue feed", async () => {
    const second = deferred<Awaited<ReturnType<typeof api.listCallIssues>>>();
    api.listCallIssues = async (input) => (
        input?.campaignId === "campaign-a"
            ? {
                items: [inboundIssue("campaign-a", "Previous campaign failure")],
                server_time: "2026-09-03T00:00:01Z",
            }
            : second.promise
    );

    const view = render(<CallIssuesPanel campaignId="campaign-a" direction="inbound" />);
    assert.ok(await screen.findByText(/Previous campaign failure/));

    view.rerender(<CallIssuesPanel campaignId="campaign-b" direction="inbound" />);

    await waitFor(() => assert.equal(screen.queryByText(/Previous campaign failure/), null));

    await act(async () => {
        second.resolve({
            items: [inboundIssue("campaign-b", "Current campaign failure")],
            server_time: "2026-09-03T00:00:02Z",
        });
        await second.promise;
    });
    assert.ok(await screen.findByText(/Current campaign failure/));
});
