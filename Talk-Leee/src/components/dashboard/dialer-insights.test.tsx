/**
 * FX1a Task 5.2 — a failed Dialer insights request shows the shared
 * ErrorState with Retry, never "Not enough call data yet.".
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { DialerInsights } from "@/components/dashboard/dialer-insights";
import { extendedApi } from "@/lib/extended-api";

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });

const bestTime = { timezone: "Europe/London", best_hour: 10, hours: [{ hour: 10, total: 4, answered: 2, answer_rate: 0.5 }] };
const retries = { attempts: [{ attempt: 1, total: 4, answered: 2, answer_rate: 0.5 }] };
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

test("a failed Dialer insights request shows the server's message with Retry, never 'Not enough call data yet.'", async () => {
    let fail = true;
    mock.method(extendedApi, "getBestTimeToCall", async () => {
        if (fail) throw new Error("Analytics are unavailable.");
        return bestTime;
    });
    mock.method(extendedApi, "getRetryEffectiveness", async () => retries);
    render(<DialerInsights />);
    await waitFor(() => assert.equal(shown("Analytics are unavailable."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(shown(/Not enough call data yet/), false);
    assert.equal(shown(/Not enough data yet/), false);

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Best time to call"), true), { timeout: SLOW });
    assert.equal(shown("Analytics are unavailable."), false);
    assert.equal(shown("Attempt 1"), true);
});

test("a successful Dialer insights request still shows the insights", async () => {
    mock.method(extendedApi, "getBestTimeToCall", async () => bestTime);
    mock.method(extendedApi, "getRetryEffectiveness", async () => retries);
    render(<DialerInsights />);
    await waitFor(() => assert.equal(shown("Best time to call"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
});
