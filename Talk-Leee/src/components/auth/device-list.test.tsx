/**
 * FX1a Task 6.2 — a failed devices read shows only the shared ErrorState with
 * Retry, without "No active devices".
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import DeviceList from "@/components/auth/device-list";
import { api } from "@/lib/api";

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });

const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

test("a failed devices read shows the server's message with Retry and never 'No active devices'", async () => {
    let fail = true;
    const read = mock.method(api, "getActiveSessions", async () => {
        if (fail) throw new Error("Sessions could not be read.");
        return { sessions: [] };
    });
    render(<DeviceList token="" />);
    await waitFor(() => assert.equal(shown("Sessions could not be read."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(shown("No active devices"), false);
    assert.equal(shown("Active Devices"), false, "only the error state is shown");

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("No active devices"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
    assert.equal(shown("Sessions could not be read."), false);
});

test("a successful empty devices read still says 'No active devices'", async () => {
    mock.method(api, "getActiveSessions", async () => ({ sessions: [] }));
    render(<DeviceList token="" />);
    await waitFor(() => assert.equal(shown("No active devices"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
});
