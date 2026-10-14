import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { SipTrunkTestResponse } from "@/lib/telephony-api";
import { TestResultPanel } from "./pbx-test-result";

afterEach(() => cleanup());

const NOW = Date.parse("2026-10-14T12:00:00Z");

function result(overrides: Partial<SipTrunkTestResponse> = {}): SipTrunkTestResponse {
    return {
        ok: true,
        status: "connected",
        verdict: "Connected. The line is registered and working.",
        tested_at: new Date(NOW - 150_000).toISOString(),
        latency_ms: 42,
        steps: [
            { key: "address", ok: true, label: "Address", detail: "Found." },
            { key: "reachability", ok: true, label: "Reachability", detail: "Answered in 42 ms." },
            { key: "credentials", ok: null, label: "Credentials", detail: "Not needed." },
            { key: "registration", ok: false, label: "Registration", detail: "Not signed in." },
        ],
        ...overrides,
    };
}

function show(state: React.ComponentProps<typeof TestResultPanel>["state"], onDismiss = () => {}) {
    return render(<TestResultPanel lineName="Main line" state={state} now={NOW} onDismiss={onDismiss} />);
}

function toneOf(overrides: Partial<SipTrunkTestResponse>) {
    cleanup();
    show({ phase: "done", result: result(overrides) });
    const className = screen.getByRole("region", { name: "Test result for Main line" }).className;
    return ["emerald", "amber", "red"].find((tone) => className.includes(tone));
}

test("the panel's colour follows the real outcome, not just ok", () => {
    assert.equal(toneOf({ status: "connected" }), "emerald");
    assert.equal(toneOf({ status: "reachable" }), "emerald");
    // A switched-off line that passes: the next step is to turn it on, not a failure.
    assert.equal(toneOf({ status: "not_registered", ok: true }), "emerald");
    assert.equal(toneOf({ status: "not_registered", ok: false }), "amber");
    assert.equal(toneOf({ status: "credentials_rejected", ok: false }), "red");
    assert.equal(toneOf({ status: "unreachable", ok: false }), "red");
    // Results saved before statuses existed fall back to ok / inconclusive.
    assert.equal(toneOf({ status: undefined, ok: true }), "emerald");
    assert.equal(toneOf({ status: undefined, ok: false, inconclusive: true }), "amber");
    assert.equal(toneOf({ status: undefined, ok: false }), "red");
});

test("each check says whether it passed, failed or was skipped, in words as well as icons", () => {
    show({ phase: "done", result: result() });
    const panel = screen.getByRole("region", { name: "Test result for Main line" });
    const texts = Array.from(panel.querySelectorAll("li[data-step]")).map((item) => item.textContent || "");
    assert.match(texts[0], /Address \(Passed\)Found\./);
    assert.match(texts[1], /Reachability \(Passed\)Answered in 42 ms\./);
    assert.match(texts[2], /Credentials \(Skipped\)Not needed\./);
    assert.match(texts[3], /Registration \(Failed\)Not signed in\./);
});

test("the footer shows when and how fast, and a SIP code only once", () => {
    const steps = [{ key: "credentials", ok: false, label: "Sign-in", detail: "Refused.", sip_code: 403 }];
    show({ phase: "done", result: result({ sip_code: "408", steps }) });
    within(screen.getByRole("region")).getByText("Tested 2 minutes ago · 42 ms · SIP 408");
    cleanup();
    // The same code already printed on the step is not repeated in the footer.
    show({ phase: "done", result: result({ sip_code: "403", steps }) });
    const panel = screen.getByRole("region", { name: "Test result for Main line" });
    within(panel).getByText("Tested 2 minutes ago · 42 ms");
    within(panel).getByText("SIP 403");
});

test("running shows a busy message and a failed request shows its reason as an alert", () => {
    show({ phase: "running" });
    assert.match(screen.getByRole("status").textContent || "", /Checking Main line/);
    cleanup();
    show({ phase: "error", message: "Wait 10 seconds before testing this line again." });
    const alert = screen.getByRole("alert");
    assert.match(alert.textContent || "", /The test didn't run/);
    assert.match(alert.textContent || "", /Wait 10 seconds before testing this line again\./);
});

test("the dismiss button reports itself", () => {
    let dismissed = 0;
    show({ phase: "done", result: result() }, () => { dismissed += 1; });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss test result for Main line" }));
    assert.equal(dismissed, 1);
});
