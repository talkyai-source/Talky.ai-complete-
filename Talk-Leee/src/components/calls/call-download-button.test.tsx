import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
    CallDownloadButton,
    capturedFieldsFromDetails,
    transcriptForExport,
    type CallDownloadButtonProps,
} from "@/components/calls/call-download-button";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import type { SingleCallExport } from "@/lib/call-history-export";
import type { Call } from "@/lib/dashboard-api";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(cleanup);

const call = {
    id: "call-abc12345", status: "completed", direction: "outbound", phone_number: "+447400123456",
    created_at: "2026-07-01T12:00:00Z", duration_seconds: 65, summary: "Short summary",
} as unknown as Call;
const entry: CallHistoryWorkflowEntry = {
    leadType: "hot",
    notes: "Call back Monday",
    form: { contact: "Sam", interest: "Upgrade", nextStep: "Send quote", completed: true },
    updatedAt: "2026-07-02T10:00:00Z",
};
const capturedFields = [{ label: "Budget", value: "5000", source: "Said by the caller", confirmed: true }];
const transcript = [
    { speaker: "Agent", text: "Hello", at: "1:00 PM" },
    { speaker: "Caller", text: "Hi there", at: "1:01 PM" },
    { speaker: "Agent", text: "Goodbye", at: "1:02 PM" },
];

function setup(overrides: Partial<CallDownloadButtonProps> = {}) {
    const built: SingleCallExport[] = [];
    const saved: Array<{ name: string; mime: string }> = [];
    render(
        <CallDownloadButton
            call={call}
            entry={entry}
            capturedFields={capturedFields}
            transcript={transcript}
            transcriptState="ready"
            timeZone="Europe/London"
            brand={{ name: "Talk-Lee", logoSrc: "/favicon.svg" }}
            buildFn={async (detail) => { built.push(detail); return new Uint8Array([1, 2, 3]); }}
            loadLogoFn={async () => null}
            saveFn={(_bytes, name, mime) => { saved.push({ name, mime }); }}
            {...overrides}
        />,
    );
    return { built, saved };
}

test("the build receives the row with notes and form, every captured field and the whole transcript", async () => {
    const { built, saved } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Download this call (PDF)" }));
    await waitFor(() => assert.equal(built.length, 1));
    const [detail] = built;
    assert.equal(detail.row.notes, "Call back Monday");
    assert.equal(detail.row.formContact, "Sam");
    assert.equal(detail.row.formNextStep, "Send quote");
    assert.equal(detail.row.leadType, "Hot");
    assert.deepEqual(detail.capturedFields, capturedFields);
    assert.deepEqual(detail.transcript, transcript);
    await waitFor(() => assert.equal(saved.length, 1));
    assert.equal(saved[0].mime, "application/pdf");
    assert.equal(/^talk-lee-call-callabc1-\d{4}-\d{2}-\d{2}\.pdf$/.test(saved[0].name), true);
});

test("the page's fuller summary replaces the row's summary", async () => {
    const { built } = setup({ summaryText: "The full, longer summary of the call." });
    fireEvent.click(screen.getByRole("button", { name: "Download this call (PDF)" }));
    await waitFor(() => assert.equal(built.length, 1));
    assert.equal(built[0].row.summary, "The full, longer summary of the call.");
});

test("while the transcript loads the button is disabled and says so", () => {
    const { built } = setup({ transcriptState: "loading" });
    const button = screen.getByRole("button", { name: "Loading the transcript…" }) as HTMLButtonElement;
    assert.equal(button.disabled, true);
    fireEvent.click(button);
    assert.equal(built.length, 0);
});

test("a transcript that failed to load still downloads, and the confirmation says without it", async () => {
    const { built } = setup({ transcriptState: "failed" });
    const button = screen.getByRole("button", { name: "Download this call (PDF)" }) as HTMLButtonElement;
    assert.equal(button.disabled, false);
    fireEvent.click(button);
    await waitFor(() => assert.equal(screen.queryByText("Downloaded without the transcript, which could not be loaded.") !== null, true));
    assert.deepEqual(built[0].transcript, []);
});

test("a call with no transcript downloads normally", async () => {
    const { built } = setup({ transcript: [] });
    fireEvent.click(screen.getByRole("button", { name: "Download this call (PDF)" }));
    await waitFor(() => assert.equal(screen.queryByText("Downloaded.") !== null, true));
    assert.equal(built.length, 1);
});

test("while building the button is disabled and reads Preparing", async () => {
    let release: (bytes: Uint8Array) => void = () => {};
    setup({ buildFn: () => new Promise<Uint8Array>((resolve) => { release = resolve; }) });
    fireEvent.click(screen.getByRole("button", { name: "Download this call (PDF)" }));
    const button = (await screen.findByRole("button", { name: "Preparing…" })) as HTMLButtonElement;
    assert.equal(button.disabled, true);
    release(new Uint8Array([1]));
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Download this call (PDF)" }) !== null, true));
});

test("a failure shows the plain sentence and nothing is saved", async () => {
    const { saved } = setup({ buildFn: async () => { throw new Error("boom"); } });
    fireEvent.click(screen.getByRole("button", { name: "Download this call (PDF)" }));
    await waitFor(() => assert.equal(screen.queryByText("The file could not be made. Try again.") !== null, true));
    assert.equal(saved.length, 0);
});

test("transcriptForExport uses Agent and Caller and the page's time text", () => {
    const out = transcriptForExport(
        [{ role: "assistant", content: "Hi", timestamp: "t1" }, { role: "user", content: "Yo", timestamp: "t2" }],
        (ts) => `at ${ts}`,
    );
    assert.deepEqual(out, [
        { speaker: "Agent", text: "Hi", at: "at t1" },
        { speaker: "Caller", text: "Yo", at: "at t2" },
    ]);
});

test("capturedFieldsFromDetails labels every filled detail with its source and drops empty ones", () => {
    const out = capturedFieldsFromDetails([
        { field_key: "company_name", field_type: "text", value: "Acme", source: "caller_stated", confirmed: true, is_required: false, updated_at: "" },
        { field_key: "budget", field_type: "text", value: null, source: "agent_inferred", confirmed: false, is_required: false, updated_at: "" },
    ]);
    assert.deepEqual(out, [{ label: "Company Name", value: "Acme", source: "Said by the caller", confirmed: true }]);
});
