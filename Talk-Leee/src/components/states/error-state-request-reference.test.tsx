/**
 * MERGE2 Task 2 — the shared ErrorState carries the server's request
 * reference: shown under the message when the failed request has one, and
 * absent (not an empty line) when it has none.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ApiClientError } from "@/lib/http-client";
import { ErrorState, failedReadRequestId } from "./page-states";

afterEach(() => { cleanup(); mock.restoreAll(); });

test("ErrorState shows the request reference under the message, with Retry", () => {
    render(<ErrorState message="Could not load campaigns." requestId="req-77" onRetry={() => {}} />);
    assert.equal(screen.getByText("Could not load campaigns.").getAttribute("role"), "alert");
    assert.ok(screen.getByText("Request reference: req-77"));
    assert.ok(screen.getByRole("button", { name: "Retry" }));
    assert.equal(screen.queryByRole("button", { name: "Try again" }), null);
});

test("ErrorState without a reference prints no reference line", () => {
    render(<ErrorState message="Could not load campaigns." onRetry={() => {}} />);
    assert.equal(screen.queryByText(/Request reference/), null);
});

test("failedReadRequestId reads the reference a failed request carries, and nothing else", () => {
    const failed = new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-9", url: "http://example.test/x", method: "GET" });
    assert.equal(failedReadRequestId(failed), "req-9");
    assert.equal(failedReadRequestId(new Error("plain")), undefined);
    assert.equal(failedReadRequestId("text"), undefined);
    assert.equal(failedReadRequestId(null), undefined);
});

test("one alert holds the whole failure: the message is its own text, and the reference and Retry are inside it", () => {
    render(<ErrorState message="Could not load campaigns." requestId="req-77" onRetry={() => {}} />);
    const alerts = screen.getAllByRole("alert");
    assert.equal(alerts.length, 1);
    assert.equal(screen.getByText("Could not load campaigns.") === alerts[0], true);
    assert.ok(within(alerts[0]).getByText("Request reference: req-77"));
    assert.ok(within(alerts[0]).getByRole("button", { name: "Retry" }));
    assert.ok(within(alerts[0]).getByRole("heading", { name: "Something went wrong" }));
});

test("the stale-data notice is shown only when the last loaded data is still on screen", () => {
    const notice = "Showing the last loaded data. It may not reflect the selected filters.";
    render(<ErrorState message="Could not load campaigns." staleData onRetry={() => {}} />);
    assert.ok(within(screen.getByRole("alert")).getByText(notice));
    cleanup();
    render(<ErrorState message="Could not load campaigns." onRetry={() => {}} />);
    assert.equal(screen.queryByText(notice), null);
});

test("offline is said in the alert and goes away on reconnect, without reading anything again", async () => {
    let online = false;
    let retries = 0;
    mock.getter(Object.getPrototypeOf(window.navigator), "onLine", () => online);
    render(<ErrorState message="Could not load campaigns." onRetry={() => { retries += 1; }} />);
    assert.ok(within(screen.getByRole("alert")).getByText("You appear to be offline. Reconnect and try again."));
    await act(async () => { online = true; window.dispatchEvent(new Event("online")); });
    assert.equal(screen.queryByText("You appear to be offline. Reconnect and try again."), null);
    assert.equal(retries, 0, "reconnecting does not retry by itself");
});

// MERGE3 Task 3.1: ReadError's remaining behaviour (read-error.tsx:29, disabled={offline || busy}).
test("Retry is disabled while the browser is offline and works again after reconnecting", async () => {
    let online = false;
    let retries = 0;
    mock.getter(Object.getPrototypeOf(window.navigator), "onLine", () => online);
    render(<ErrorState message="Could not load campaigns." onRetry={() => { retries += 1; }} />);
    const retry = () => screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement;
    assert.equal(retry().disabled, true);
    fireEvent.click(retry());
    assert.equal(retries, 0, "nothing is read while offline");
    await act(async () => { online = true; window.dispatchEvent(new Event("online")); });
    assert.equal(retry().disabled, false);
    fireEvent.click(retry());
    assert.equal(retries, 1);
});

test("Retry is disabled while the retry it started is still running, so it cannot be sent twice", async () => {
    let settle!: () => void;
    let retries = 0;
    render(<ErrorState message="Could not load campaigns." onRetry={() => { retries += 1; return new Promise<void>((resolve) => { settle = resolve; }); }} />);
    const retry = () => screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement;
    assert.equal(retry().disabled, false);
    fireEvent.click(retry());
    assert.equal(retry().disabled, true);
    fireEvent.click(retry());
    assert.equal(retries, 1);
    await act(async () => { settle(); });
    assert.equal(retry().disabled, false);
});

test("Retry is disabled while the caller says its read is running again", () => {
    const { rerender } = render(<ErrorState message="Could not load campaigns." busy onRetry={() => {}} />);
    assert.equal((screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement).disabled, true);
    rerender(<ErrorState message="Could not load campaigns." onRetry={() => {}} />);
    assert.equal((screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement).disabled, false);
});
