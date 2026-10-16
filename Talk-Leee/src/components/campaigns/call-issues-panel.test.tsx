import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function outboundIssue(over: Partial<CallIssue>): CallIssue {
    return {
        job_id: "job-1",
        phone_number: "+15550001111",
        campaign_id: "campaign-out",
        status: "pending",
        reason_code: "outside_time_window_14:00_17:00",
        category: null,
        title: "Outside calling hours",
        suggestion: "It will dial automatically when the window opens.",
        severity: "warning",
        stage: "schedule",
        attempts: 1,
        updated_at: "2026-10-16T08:00:00Z",
        ...over,
    };
}

async function expandSummary() {
    fireEvent.click(await screen.findByRole("button", { name: /Call issues/ }));
}

test("an outbound problem shows the dialer's own reason and when it is next eligible", async () => {
    api.listCallIssues = async () => ({
        items: [
            outboundIssue({
                block_code: "schedule_outside_window",
                block_message: "Outside this campaign's calling hours (Mon & Fri, 14:00-17:00 Europe/London).",
                next_eligible_at: "2026-10-19T13:00:00Z",
            }),
        ],
        server_time: "2026-10-16T08:00:05Z",
    });

    render(<CallIssuesPanel campaignId="campaign-out" direction="outbound" />);
    await expandSummary();

    assert.ok(await screen.findByText(/Reason: Outside this campaign's calling hours/));
    assert.ok(screen.getByText(/Next attempt:/));
});

test("without a block message the raw reason code is shown instead of nothing", async () => {
    api.listCallIssues = async () => ({
        items: [outboundIssue({ reason_code: "rejected", block_message: null, block_code: null })],
        server_time: "2026-10-16T08:00:05Z",
    });

    render(<CallIssuesPanel campaignId="campaign-out" />);
    await expandSummary();

    assert.ok(await screen.findByText("Reason: rejected"));
    assert.equal(screen.queryByText(/Next attempt:/), null);
});

test("the inbound issue panel does not gain a reason line", async () => {
    api.listCallIssues = async () => ({
        items: [inboundIssue("campaign-in", "Inbound call processing failed")],
        server_time: "2026-10-16T08:00:05Z",
    });

    render(<CallIssuesPanel campaignId="campaign-in" direction="inbound" />);
    await expandSummary();

    assert.ok((await screen.findAllByText(/Inbound call processing failed/)).length >= 2, "group row is expanded");
    assert.equal(screen.queryByText(/Reason:/), null);
});

test("a generic catch-all block message does not hide the specific reason code", async () => {
    api.listCallIssues = async () => ({
        items: [
            outboundIssue({
                reason_code: "no_ready_trunk",
                block_code: "originate_failed",
                block_message: "The call could not be placed. If it doesn't clear on its own, check the campaign's settings.",
            }),
        ],
        server_time: "2026-10-16T08:00:05Z",
    });

    render(<CallIssuesPanel campaignId="campaign-out" direction="outbound" />);
    await expandSummary();

    assert.ok(await screen.findByText("Reason: no_ready_trunk"));
    assert.equal(screen.queryByText(/The call could not be placed/), null);
});
