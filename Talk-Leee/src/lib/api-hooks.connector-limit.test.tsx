/**
 * FX2a Task 4.2 — the connector status check and Connect/Reconnect end as a
 * failure 20 seconds after they started, retries included, when the server
 * never answers. Runs the real hooks and the real client on a mocked clock.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";

import { CONNECTOR_TOTAL_LIMIT_MS, useAuthorizeConnector, useConnectorStatuses } from "@/lib/api-hooks";

const originalFetch = globalThis.fetch;
let client: QueryClient | null = null;
let requests = 0;

afterEach(() => {
    cleanup();
    client?.clear();
    client = null;
    mock.timers.reset();
    globalThis.fetch = originalFetch;
});

// A server that never answers: a request only ends when the client aborts it.
function neverAnswers() {
    requests = 0;
    globalThis.fetch = ((_url: RequestInfo | URL, init?: RequestInit) => {
        requests += 1;
        return new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(init.signal!.reason));
        });
    }) as typeof fetch;
}
const settle = async () => {
    for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setImmediate(resolve));
};
// React Query hands its state updates to a zero-delay timer, which the mocked
// clock holds back: run the timers that are due now, without moving the clock.
const flush = () => act(async () => { await settle(); mock.timers.tick(0); await settle(); mock.timers.tick(0); await settle(); });
// Advances the mocked clock in small steps so each retry is scheduled at its real time.
async function advanceTo(clock: { now: number }, target: number) {
    while (clock.now < target) {
        const step = Math.min(250, target - clock.now);
        await act(async () => { mock.timers.tick(step); await settle(); });
        clock.now += step;
        await flush();
    }
}

function mount(node: React.ReactElement) {
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
    client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity }, mutations: { gcTime: Infinity } } });
    render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function StatusProbe() {
    const q = useConnectorStatuses();
    return <div data-testid="state">{q.isError ? `failed: ${(q.error as Error).message}` : q.data ? "answered" : "checking"}</div>;
}

function ConnectProbe() {
    const authorize = useAuthorizeConnector();
    return (
        <>
            <button onClick={() => authorize.mutate({ type: "calendar", redirect_uri: "http://localhost:3000/connectors/calendar/callback" })}>Connect</button>
            <div data-testid="state">{authorize.isError ? `failed: ${(authorize.error as Error).message}` : authorize.isPending ? "starting" : "idle"}</div>
        </>
    );
}

const state = () => screen.getByTestId("state").textContent;

test("the limit is 20 seconds", () => {
    assert.equal(CONNECTOR_TOTAL_LIMIT_MS, 20_000);
});

test("the status check ends as a failure 20 seconds after it started, retries included", async () => {
    neverAnswers();
    mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    const clock = { now: 0 };
    mount(<StatusProbe />);
    await flush();
    assert.equal(state(), "checking");

    await advanceTo(clock, 19_750);
    assert.equal(state(), "checking", "not ended before 20 s");
    assert.equal(requests, 2, "the first attempt timed out at 12 s and one retry is running");

    await advanceTo(clock, 20_000);
    assert.equal(state(), "failed: Request timed out", "ended as a failure at 20 s");

    // Nothing is still retrying behind the failure state. (The page's own
    // 10-second re-check of the status is a separate, later request.)
    await advanceTo(clock, 29_000);
    assert.equal(requests, 2, "no further attempt after the limit");
});

test("Connect ends as a failure 20 seconds after it started, its retry included", async () => {
    neverAnswers();
    mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    const clock = { now: 0 };
    mount(<ConnectProbe />);
    await act(async () => { screen.getByRole("button", { name: "Connect" }).click(); await settle(); });
    await flush();
    assert.equal(state(), "starting");

    await advanceTo(clock, 19_750);
    assert.equal(state(), "starting", "not ended before 20 s");
    assert.equal(requests, 2, "the first attempt timed out at 12 s and the one retry is running");

    await advanceTo(clock, 20_000);
    assert.equal(state(), "failed: Request timed out", "ended as a failure at 20 s");

    await advanceTo(clock, 45_000);
    assert.equal(requests, 2, "no further attempt after the limit");
    assert.equal(state(), "failed: Request timed out");
});
