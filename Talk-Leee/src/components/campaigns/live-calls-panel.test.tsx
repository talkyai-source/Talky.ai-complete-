import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import {
    LiveCallsPanel,
    type LiveCallsPanelProps,
    isTerminalLiveCall,
    terminationView,
} from "@/components/campaigns/live-calls-panel";
import {
    api,
    HangupCallResponseSchema,
    type HangupCallResponse,
    type LiveCallItem,
} from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import { ApiClientError } from "@/lib/http-client";
import { inboundQueryKeys } from "@/lib/queries/inbound-queries";

const originalListLiveCalls = api.listLiveCalls;
const originalHangupCall = api.hangupCall;
const originalGetCall = dashboardApi.getCall;
const originalFetchRecording = extendedApi.fetchRecordingPlaybackBlob;
const objectUrls = globalThis.URL as unknown as {
    createObjectURL?: (blob: Blob) => string;
    revokeObjectURL?: (url: string) => void;
};
const originalCreateObjectURL = objectUrls.createObjectURL;
const originalRevokeObjectURL = objectUrls.revokeObjectURL;
const queryClients: QueryClient[] = [];

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

afterEach(() => {
    cleanup();
    for (const queryClient of queryClients.splice(0)) queryClient.clear();
    api.listLiveCalls = originalListLiveCalls;
    api.hangupCall = originalHangupCall;
    dashboardApi.getCall = originalGetCall;
    extendedApi.fetchRecordingPlaybackBlob = originalFetchRecording;
    objectUrls.createObjectURL = originalCreateObjectURL;
    objectUrls.revokeObjectURL = originalRevokeObjectURL;
});

function activeCall(overrides: Partial<LiveCallItem> = {}): LiveCallItem {
    return {
        id: "call-1",
        to_number: "+15550001111",
        caller_id: "+15550002222",
        status: "in_call",
        started_at: "2026-08-26T10:00:00.000Z",
        answered_at: "2026-08-26T10:00:03.000Z",
        termination_status: "none",
        ...overrides,
    };
}

function hangupResponse(overrides: Partial<HangupCallResponse> = {}): HangupCallResponse {
    return {
        status: "requested",
        call_id: "call-1",
        call_status: "in_call",
        termination_status: "requested",
        provider_hangup_requested: true,
        provider_hangup_confirmed: false,
        provider_hangup_error: null,
        ...overrides,
    };
}

function renderPanel(permissions: string[] = [], props: LiveCallsPanelProps = {}) {
    const queryClient = new QueryClient({
        defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
        },
    });
    queryClients.push(queryClient);
    queryClient.setQueryData(inboundQueryKeys.permissions, { permissions });
    const rendered = render(
        <QueryClientProvider client={queryClient}>
            <LiveCallsPanel {...props} />
        </QueryClientProvider>,
    );
    return {
        ...rendered,
        rerenderPanel(nextProps: LiveCallsPanelProps) {
            rendered.rerender(
                <QueryClientProvider client={queryClient}>
                    <LiveCallsPanel {...nextProps} />
                </QueryClientProvider>,
            );
        },
    };
}

test("hangup responses require the confirmation-aware contract", () => {
    assert.equal(HangupCallResponseSchema.parse(hangupResponse()).termination_status, "requested");
    assert.throws(
        () => HangupCallResponseSchema.parse({ status: "ended", call_id: "call-1" }),
        /Invalid input|expected/i,
    );
});

test("termination presentation remains pending until the call itself is terminal", () => {
    const pending = terminationView(activeCall({ termination_status: "requested" }));
    assert.equal(pending.phase, "pending");
    assert.match(pending.message ?? "", /Awaiting provider confirmation/);

    assert.equal(isTerminalLiveCall("completed"), true);
    assert.equal(
        terminationView(activeCall({ status: "completed", termination_status: "requested" })).phase,
        "none",
    );
});

test("inbound live rows show direction, ANI, DID, admission, and consent", async () => {
    api.listLiveCalls = async () => ({
        items: [activeCall({
            direction: "inbound",
            caller_ani: "+15550003333",
            called_did: "+15550004444",
            admission_status: "allowed",
            consent_status: "not_required",
        })],
        server_time: "2026-08-26T10:00:10.000Z",
    });

    renderPanel();

    assert.ok(await screen.findByText("Inbound"));
    assert.ok(screen.getByText("+15550003333"));
    assert.ok(screen.getByText("+15550004444"));
    assert.ok(screen.getByText(/Admission: allowed/));
    assert.ok(screen.getByText(/Consent: not required/));
});

test("an inbound campaign panel scopes every poll by campaign and direction", async () => {
    let received: Parameters<typeof api.listLiveCalls>[0];
    api.listLiveCalls = async (input) => {
        received = input;
        return {
            items: [],
            server_time: "2026-08-26T10:00:10.000Z",
        };
    };

    renderPanel([], { campaignId: "campaign-inbound", direction: "inbound" });

    await waitFor(() => assert.equal(received?.campaignId, "campaign-inbound"));
    assert.equal(received?.direction, "inbound");
});

test("a superseded campaign poll cannot replace the current live calls", async () => {
    const first = deferred<Awaited<ReturnType<typeof api.listLiveCalls>>>();
    const requested: Array<string | undefined> = [];
    api.listLiveCalls = async (input) => {
        requested.push(input?.campaignId);
        if (input?.campaignId === "campaign-a") return first.promise;
        return {
            items: [activeCall({ id: "call-b", to_number: "+15550000002" })],
            server_time: "2026-09-03T00:00:02Z",
        };
    };

    const view = renderPanel([], {
        campaignId: "campaign-a",
        direction: "inbound",
    });
    await waitFor(() => assert.deepEqual(requested, ["campaign-a"]));

    view.rerenderPanel({ campaignId: "campaign-b", direction: "inbound" });
    assert.ok(await screen.findByText("+15550000002"));

    await act(async () => {
        first.resolve({
            items: [activeCall({ id: "call-a", to_number: "+15550000001" })],
            server_time: "2026-09-03T00:00:01Z",
        });
        await first.promise;
    });

    await waitFor(() => {
        assert.ok(screen.getByText("+15550000002"));
        assert.equal(screen.queryByText("+15550000001"), null);
    });
});

test("switching campaigns immediately clears loaded rows and their local action state", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    const second = deferred<Awaited<ReturnType<typeof api.listLiveCalls>>>();
    api.listLiveCalls = async (input) => (
        input?.campaignId === "campaign-a"
            ? {
                items: [activeCall({ id: "call-a", to_number: "+15550000001" })],
                server_time: "2026-09-03T00:00:01Z",
            }
            : second.promise
    );
    api.hangupCall = async () => new Promise<HangupCallResponse>(() => {});

    const view = renderPanel([], { campaignId: "campaign-a", direction: "inbound" });
    const oldHangup = await screen.findByRole("button", { name: "Hang up call to +15550000001" });
    await user.click(oldHangup);
    assert.ok(await screen.findByText("Ending"));

    view.rerenderPanel({ campaignId: "campaign-b", direction: "inbound" });

    await waitFor(() => assert.equal(screen.queryByText("+15550000001"), null));
    assert.equal(screen.queryByText("Ending"), null);
    assert.equal(screen.queryByRole("button", { name: "Hang up call to +15550000001" }), null);

    await act(async () => {
        second.resolve({
            items: [activeCall({ id: "call-b", to_number: "+15550000002" })],
            server_time: "2026-09-03T00:00:02Z",
        });
        await second.promise;
    });
    assert.ok(await screen.findByText("+15550000002"));
});

test("a hangup request shows Ending without optimistically ending the row or allowing a duplicate", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    api.listLiveCalls = async () => ({
        items: [activeCall()],
        server_time: "2026-08-26T10:00:10.000Z",
    });
    let resolveHangup!: (value: HangupCallResponse) => void;
    let hangupCalls = 0;
    api.hangupCall = async () => {
        hangupCalls += 1;
        return new Promise<HangupCallResponse>((resolve) => {
            resolveHangup = resolve;
        });
    };

    renderPanel();
    const hangup = await screen.findByRole("button", { name: "Hang up call to +15550001111" });
    await user.click(hangup);

    const ending = await screen.findByRole("button", { name: "Ending call to +15550001111" });
    assert.equal((ending as HTMLButtonElement).disabled, true);
    assert.ok(screen.getByText("Ending"));
    assert.equal(screen.queryByText("Ended"), null);
    await user.click(ending);
    assert.equal(hangupCalls, 1);

    resolveHangup(hangupResponse());
    await waitFor(() => assert.ok(screen.getByText(/Awaiting provider confirmation/)));
    assert.equal(hangupCalls, 1);
});

test("a failed or unconfirmed hangup exposes the provider error and allows retry", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    api.listLiveCalls = async () => ({
        items: [activeCall()],
        server_time: "2026-08-26T10:00:10.000Z",
    });
    let hangupCalls = 0;
    api.hangupCall = async () => {
        hangupCalls += 1;
        return hangupCalls === 1
            ? hangupResponse({
                status: "failed",
                termination_status: "failed",
                provider_hangup_requested: false,
                provider_hangup_error: "Provider channel could not be found",
            })
            : hangupResponse();
    };

    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Hang up call to +15550001111" }));

    const retry = await screen.findByRole("button", { name: "Retry hangup for call to +15550001111" });
    assert.ok(screen.getByRole("alert").textContent?.includes("Provider channel could not be found"));
    assert.equal((retry as HTMLButtonElement).disabled, false);

    await user.click(retry);
    await waitFor(() => assert.equal(hangupCalls, 2));
    assert.ok(screen.getByText(/Awaiting provider confirmation/));
});

test("an HTTP unconfirmed response surfaces its structured provider detail", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    api.listLiveCalls = async () => ({
        items: [activeCall()],
        server_time: "2026-08-26T10:00:10.000Z",
    });
    api.hangupCall = async () => {
        throw new ApiClientError({
            status: 504,
            code: "termination_unconfirmed",
            message: "Server error",
            url: "/calls/call-1/hangup",
            method: "POST",
            details: {
                reason: "confirmation_timeout",
                provider_hangup_error: "Provider confirmation timed out",
            },
        });
    };

    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Hang up call to +15550001111" }));

    assert.ok(await screen.findByText("Provider confirmation timed out"));
    assert.ok(screen.getByRole("button", { name: "Retry hangup for call to +15550001111" }));
});

function endedCall(): LiveCallItem {
    return activeCall({
        status: "completed",
        outcome: "agent_hung_up",
        ended_at: "2026-08-26T10:00:12.000Z",
        duration_seconds: 9,
        termination_status: "confirmed",
    });
}

/** Stub the media plumbing and return the list of revoked object URLs. */
function stubRecordingMedia(): { created: string[]; revoked: string[] } {
    const created: string[] = [];
    const revoked: string[] = [];
    let seq = 0;
    objectUrls.createObjectURL = () => {
        seq += 1;
        const url = `blob:recording-${seq}`;
        created.push(url);
        return url;
    };
    objectUrls.revokeObjectURL = (url: string) => {
        revoked.push(url);
    };
    extendedApi.fetchRecordingPlaybackBlob = async () =>
        new Blob([new Uint8Array([1, 2, 3])], { type: "audio/wav" });
    api.listLiveCalls = async () => ({
        items: [endedCall()],
        server_time: "2026-08-26T10:00:12.000Z",
    });
    dashboardApi.getCall = async () => ({
        id: "call-1",
        campaign_id: "",
        lead_id: "",
        phone_number: "+15550001111",
        status: "completed",
        created_at: "2026-08-26T10:00:00.000Z",
        transcript: "Agent: hello.",
        recording_id: "rec-1",
    });
    return { created, revoked };
}

async function openRecordingPlayer(container: HTMLElement) {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    await user.click(await screen.findByText("+15550001111"));
    await user.click(await screen.findByRole("button", { name: "Load recording" }));
    return await waitFor(() => {
        const audio = container.querySelector("audio");
        assert.ok(audio, "expected an audio element after loading the recording");
        return audio;
    });
}

// Playback authorization: `recordings:download` cannot be enforced in the
// browser (see the SECURITY NOTE in live-calls-panel.tsx). What the component
// MUST do is avoid leaving a freely copyable object URL lying around for a user
// who is not allowed to download — the handle exists only while audio plays.
test("without download permission the recording object URL is revoked when playback stops", async () => {
    const { created, revoked } = stubRecordingMedia();

    const { container } = renderPanel(["recordings:read"]);
    const audio = await openRecordingPlayer(container);

    assert.equal(created.length, 1);
    assert.equal(audio.getAttribute("src"), created[0]);
    assert.equal(audio.getAttribute("controlsList"), "nodownload");
    assert.deepEqual(revoked, []);

    fireEvent.pause(audio);

    await waitFor(() => assert.deepEqual(revoked, [created[0]]));
    // The player is gone, so the page no longer holds a usable handle to the
    // audio; playing again re-fetches through the authorized endpoint.
    await waitFor(() => assert.equal(container.querySelector("audio"), null));
    assert.ok(screen.getByRole("button", { name: "Load recording" }));
});

test("with download permission playback pausing keeps the loaded recording", async () => {
    const { created, revoked } = stubRecordingMedia();

    const { container } = renderPanel(["recordings:read", "recordings:download"]);
    const audio = await openRecordingPlayer(container);

    assert.equal(audio.getAttribute("controlsList"), null);

    fireEvent.pause(audio);

    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(revoked, []);
    assert.equal(container.querySelector("audio")?.getAttribute("src"), created[0]);
});

test("polling a terminal call clears Ending and uses the server's final status", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    let polls = 0;
    api.listLiveCalls = async () => {
        polls += 1;
        return {
            items: polls === 1
                ? [activeCall()]
                : [activeCall({
                    status: "completed",
                    outcome: "agent_hung_up",
                    ended_at: "2026-08-26T10:00:12.000Z",
                    duration_seconds: 9,
                    termination_status: "confirmed",
                })],
            server_time: "2026-08-26T10:00:12.000Z",
        };
    };
    api.hangupCall = async () => hangupResponse();
    dashboardApi.getCall = async () => ({
        id: "call-1",
        campaign_id: "",
        lead_id: "",
        phone_number: "+15550001111",
        status: "completed",
        created_at: "2026-08-26T10:00:00.000Z",
    });

    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Hang up call to +15550001111" }));
    assert.ok(await screen.findByText("Ending"));

    await waitFor(() => assert.ok(screen.getByText("Agent ended")), { timeout: 3_500 });
    assert.equal(screen.queryByText("Ending"), null);
    assert.ok(polls >= 2);
});
