/**
 * FX1a Task 6.1 — a failed recording-policy read shows the shared ErrorState
 * with Retry and no form: never a default policy that may be untrue.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RecordingPolicySection } from "@/components/settings/recording-policy-section";
import { setRequestIdentity, setTokenProvider } from "@/lib/http-client";

const SLOW = 15_000;
const originalFetch = globalThis.fetch;
afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    setTokenProvider(null);
});

const POLICY = {
    configured: true, default_consent_mode: "two_party", announcement_text: "This call is recorded.", opt_out_dtmf_digit: "9",
    two_party_country_codes: ["GB"], retention_days: 30, updated_at: "2026-01-01T00:00:00Z", effect: "Calls are recorded after a spoken notice.",
};
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const formShown = () => screen.queryByRole("combobox", { name: "Recording consent mode" }) !== null
    || screen.queryByLabelText("Recording consent mode") !== null;

test("a failed recording-policy read shows the server's message with Retry and no form", async () => {
    let fail = true;
    let reads = 0;
    setTokenProvider(() => null);
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url) => {
        assert.equal(String(url).endsWith("/recordings/policy"), true);
        reads++;
        return fail ? Response.json({ detail: "Recording policy store is unavailable." }, { status: 503 }) : Response.json(POLICY);
    }) as typeof fetch;

    render(<RecordingPolicySection canEdit />);
    await waitFor(() => assert.equal(shown("Recording policy store is unavailable."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(formShown(), false, "no form while the policy is unknown");
    assert.equal(shown("Do not record calls"), false, "no default policy is claimed");
    assert.equal(screen.queryByTestId("recording-policy") === null, true);
    assert.equal(screen.queryByRole("button", { name: /Save/ }) === null, true);

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Calls are recorded after a spoken notice."), true), { timeout: SLOW });
    assert.equal(reads, 2);
    assert.equal(shown("Recording policy store is unavailable."), false);
    assert.equal(screen.queryByTestId("recording-policy") !== null, true, "the form returns with the real policy");
});
