import React from "react";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import type { CallWorkflowPatch } from "@/lib/use-call-history-workflow";
import type { correctCallerGave } from "@/lib/call-contact-correction";
import { formatTimestamp } from "@/lib/timestamps";
import { CallHistoryTable } from "./call-history-table";

afterEach(() => cleanup());

const call: Call = {
    id: "c1", campaign_id: "k1", campaign_name: "Spring", lead_id: "", phone_number: "+15550001111", from_number: "+442071234567",
    status: "completed", outcome: "no_answer", duration_seconds: 75, direction: "outbound", recording_id: "r1",
    created_at: "2026-11-06T10:00:00.000Z", captured_email: "old@example.com", captured_phone: "+923120750496",
} as Call;
const call2: Call = { ...call, id: "c2", campaign_name: "Summer", captured_email: null, captured_phone: null, recording_id: undefined } as Call;
const entry: CallHistoryWorkflowEntry = {
    leadType: "warm", notes: "first note", form: { contact: "Sam", interest: "Quote", nextStep: "Call back", completed: true }, updatedAt: "x",
};
type Patch = { call: Call; patch: CallWorkflowPatch };

function setup(over: { canEdit?: boolean; correctFn?: typeof correctCallerGave } = {}) {
    const patches: Patch[] = [];
    let corrected = 0;
    render(
        <CallHistoryTable
            calls={[call, call2]}
            workflow={{ c1: entry }}
            timeZone="Europe/London"
            canEdit={over.canEdit ?? true}
            onWorkflowChange={(c, patch) => patches.push({ call: c, patch })}
            onContactCorrected={() => { corrected += 1; }}
            correctFn={over.correctFn}
        />,
    );
    return { patches, corrected: () => corrected };
}
const cell = (r: number, c: number) => document.querySelector(`[data-cell="${r}-${c}"]`) as HTMLElement;
const NOTES = 8, FORM_INTEREST = 10, FORM_NEXT = 11, EMAIL = 12, NUMBER = 13, DATE = 0;

test("columns render in order with the list's formatted values", () => {
    setup();
    const headers = Array.from(document.querySelectorAll("th")).map((th) => th.textContent);
    assert.deepEqual(headers, [
        "Date and time", "Direction", "Campaign", "From", "To", "Result", "Duration", "Lead type ✎", "Notes ✎",
        "Follow-up: contact ✎", "Follow-up: interest ✎", "Follow-up: next step ✎", "Email given ✎", "Number given ✎", "Recording", "",
    ]);
    const texts = Array.from(document.querySelectorAll("tbody tr:first-child td")).map((td) => td.textContent);
    assert.deepEqual(texts, [
        formatTimestamp(call.created_at, "Europe/London").label, "Outbound", "Spring", "+442071234567", "+15550001111", "No Answer", "1:15",
        "Warm", "first note", "Sam", "Quote", "Call back", "old@example.com", "+92 312 0750496", "Yes", "Details",
    ]);
    assert.equal(cell(1, EMAIL).textContent, "Not provided");
    assert.equal(screen.getAllByRole("link", { name: "Details" })[0].getAttribute("href"), "/calls/c1");
    assert.equal(document.querySelectorAll("tbody tr").length, 2);
});

test("Enter edits a notes cell, Enter saves the right patch and moves down, Esc restores", () => {
    const { patches } = setup();
    fireEvent.keyDown(cell(0, NOTES), { key: "Enter" });
    const input = screen.getByLabelText("Notes for +15550001111") as HTMLInputElement;
    assert.equal(input.value, "first note");
    assert.equal(input.maxLength, 4000);
    fireEvent.change(input, { target: { value: "changed" } });
    fireEvent.keyDown(input, { key: "Escape" });
    assert.equal(screen.queryByLabelText("Notes for +15550001111") === null, true);
    assert.equal(cell(0, NOTES).textContent, "first note");
    assert.equal(patches.length, 0);
    fireEvent.keyDown(cell(0, NOTES), { key: "F2" });
    const again = screen.getByLabelText("Notes for +15550001111");
    fireEvent.change(again, { target: { value: "changed" } });
    fireEvent.keyDown(again, { key: "Enter" });
    assert.deepEqual(patches.map((p) => [p.call.id, p.patch]), [["c1", { notes: "changed" }]]);
    assert.equal(document.activeElement === cell(1, NOTES), true);
});

test("a follow-up field sends the whole form with one field changed", () => {
    const { patches } = setup();
    fireEvent.click(cell(0, FORM_INTEREST));
    const input = screen.getByLabelText("Follow-up: interest for +15550001111");
    assert.equal((input as HTMLInputElement).maxLength, 1000);
    fireEvent.change(input, { target: { value: "Demo" } });
    fireEvent.keyDown(input, { key: "Enter" });
    assert.deepEqual(patches[0].patch, { form: { contact: "Sam", interest: "Demo", nextStep: "Call back", completed: true } });
});

test("lead type is a select that saves the chosen value", () => {
    const { patches } = setup();
    fireEvent.click(cell(0, 7));
    fireEvent.change(screen.getByLabelText("Lead type for +15550001111"), { target: { value: "hot" } });
    assert.deepEqual(patches.map((p) => p.patch), [{ leadType: "hot" }]);
});

test("read-only cells cannot be edited", () => {
    setup();
    fireEvent.click(cell(0, DATE));
    fireEvent.keyDown(cell(0, DATE), { key: "Enter" });
    assert.equal(document.querySelectorAll("input, select").length, 0);
    assert.equal(cell(0, DATE).getAttribute("data-editable"), "false");
    assert.equal(cell(0, NOTES).getAttribute("data-editable"), "true");
});

test("with canEdit false nothing is editable and the line says so", () => {
    const { patches } = setup({ canEdit: false });
    for (const c of [7, NOTES, FORM_INTEREST, EMAIL, NUMBER]) {
        fireEvent.click(cell(0, c));
        fireEvent.keyDown(cell(0, c), { key: "Enter" });
    }
    assert.equal(document.querySelectorAll("input, select").length, 0);
    assert.equal(patches.length, 0);
    assert.equal(document.querySelectorAll('[data-editable="true"]').length, 0);
    screen.getByText("You can view this table. Your role cannot edit call details.");
});

test("a bad email shows the sentence and sends nothing", () => {
    const sent: unknown[][] = [];
    setup({ correctFn: (async (...args: unknown[]) => { sent.push(args); }) as typeof correctCallerGave });
    fireEvent.click(cell(0, EMAIL));
    const input = screen.getByLabelText("Email given for +15550001111");
    fireEvent.change(input, { target: { value: "not-an-email" } });
    fireEvent.keyDown(input, { key: "Enter" });
    screen.getByText("Enter a full email address, such as name@example.com.");
    assert.equal(sent.length, 0);
});

test("a good email calls the helper with (id, email, value) then onContactCorrected", async () => {
    const sent: unknown[][] = [];
    const { corrected } = setup({ correctFn: (async (...args: unknown[]) => { sent.push(args); }) as typeof correctCallerGave });
    fireEvent.click(cell(0, EMAIL));
    const input = screen.getByLabelText("Email given for +15550001111");
    fireEvent.change(input, { target: { value: "new@example.com" } });
    fireEvent.keyDown(input, { key: "Enter" });
    assert.deepEqual(sent, [["c1", "email", "new@example.com"]]);
    await waitFor(() => assert.equal(corrected(), 1));
});

test("a failed save keeps the typed value and shows Not saved with the server's message", async () => {
    setup({ correctFn: (async () => { throw new Error("The server is busy."); }) as typeof correctCallerGave });
    fireEvent.click(cell(0, NUMBER));
    const input = screen.getByLabelText("Number given for +15550001111");
    fireEvent.change(input, { target: { value: "+447429916656" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => assert.equal((cell(0, NUMBER).textContent ?? "").includes("Not saved. Try again. The server is busy."), true));
    assert.equal((cell(0, NUMBER).textContent ?? "").includes("+44 7429 916656"), true);
});

test("clearing an email sends an empty value and shows Not provided", async () => {
    const sent: unknown[][] = [];
    setup({ correctFn: (async (...args: unknown[]) => { sent.push(args); }) as typeof correctCallerGave });
    fireEvent.click(cell(0, EMAIL));
    const input = screen.getByLabelText("Email given for +15550001111");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.keyDown(input, { key: "Enter" });
    assert.deepEqual(sent, [["c1", "email", ""]]);
    await act(async () => {});
    assert.equal(cell(0, EMAIL).textContent?.includes("Not provided"), true);
});

test("Tab saves and moves to the next editable cell, Shift+Tab back", () => {
    const { patches } = setup();
    fireEvent.click(cell(0, FORM_INTEREST));
    const input = screen.getByLabelText("Follow-up: interest for +15550001111");
    fireEvent.change(input, { target: { value: "Demo" } });
    fireEvent.keyDown(input, { key: "Tab" });
    assert.equal(patches.length, 1);
    assert.equal(document.activeElement === cell(0, FORM_NEXT), true);
    fireEvent.keyDown(cell(0, FORM_NEXT), { key: "Enter" });
    fireEvent.keyDown(screen.getByLabelText("Follow-up: next step for +15550001111"), { key: "Tab", shiftKey: true });
    assert.equal(document.activeElement === cell(0, FORM_INTEREST), true);
});

test("arrow keys move between cells when not editing", () => {
    setup();
    cell(0, 0).focus();
    fireEvent.keyDown(cell(0, 0), { key: "ArrowRight" });
    assert.equal(document.activeElement === cell(0, 1), true);
    fireEvent.keyDown(cell(0, 1), { key: "ArrowDown" });
    assert.equal(document.activeElement === cell(1, 1), true);
});
