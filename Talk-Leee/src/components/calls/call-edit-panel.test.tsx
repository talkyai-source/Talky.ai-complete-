import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CallEditPanel, type CallEditPanelProps } from "@/components/calls/call-edit-panel";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import type { Call } from "@/lib/dashboard-api";
import type { CallWorkflowPatch } from "@/lib/use-call-history-workflow";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
// jsdom has no layout engine; the shared Select scrolls its active option into view.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

afterEach(cleanup);

const call = {
    id: "call-1", status: "completed", direction: "outbound", phone_number: "+447400123456",
    created_at: "2026-07-01T12:00:00Z", captured_email: "old@example.com", captured_phone: "+447400111222",
} as unknown as Call;
const plainCall = { ...call, captured_email: undefined, captured_phone: undefined } as unknown as Call;
const entry: CallHistoryWorkflowEntry = {
    leadType: "warm",
    notes: "Wants a quote",
    form: { contact: "Sam", interest: "Plan upgrade", nextStep: "Call Friday", completed: false },
    updatedAt: "2026-07-02T10:00:00Z",
};

function setup(overrides: Partial<CallEditPanelProps> = {}) {
    const patches: CallWorkflowPatch[] = [];
    const corrections: Array<[string, string, string]> = [];
    let saved = 0;
    let retried = 0;
    const props: CallEditPanelProps = {
        call,
        entry,
        canEdit: true,
        busy: false,
        failed: false,
        onRetry: () => { retried += 1; },
        onWorkflowChange: (_call, patch) => { patches.push(patch); },
        onSaved: () => { saved += 1; },
        correctFn: async (id, kind, value) => { corrections.push([id, kind, value]); },
        ...overrides,
    };
    render(<CallEditPanel {...props} />);
    return { patches, corrections, saved: () => saved, retried: () => retried, user: userEvent.setup() };
}

test("read mode shows the saved notes, follow-up fields and completion", () => {
    setup();
    assert.equal(screen.queryByRole("heading", { name: "Notes and follow-up" }) !== null, true);
    assert.equal(screen.queryByText("Wants a quote") !== null, true);
    assert.equal(screen.queryByText("Sam") !== null, true);
    assert.equal(screen.queryByText("Plan upgrade") !== null, true);
    assert.equal(screen.queryByText("Call Friday") !== null, true);
    assert.equal(screen.queryByText("Not marked complete") !== null, true);
    assert.equal(screen.queryByText(/Warm/) !== null, true);
    assert.equal(screen.queryByText("(suggested)") === null, true);
});

test("with nothing saved, the suggested lead type and empty wording show", () => {
    setup({ entry: undefined, call: plainCall });
    assert.equal(screen.queryByText("(suggested)") !== null, true);
    assert.equal(screen.queryByText("No notes yet") !== null, true);
    assert.equal(screen.getAllByText("Not filled in").length, 3);
});

test("a read-only member sees no Edit button and the role sentence", () => {
    setup({ canEdit: false });
    assert.equal(screen.queryByRole("button", { name: "Edit" }) === null, true);
    assert.equal(screen.queryByText("Your role cannot edit call details.") !== null, true);
});

test("a person who may edit sees Edit, and edit mode focuses the first field", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await waitFor(() => assert.equal(document.activeElement === screen.getByRole("combobox", { name: /lead type for/i }), true));
    assert.equal((screen.getByLabelText("Email the caller gave") as HTMLInputElement).value, "old@example.com");
    assert.equal((screen.getByLabelText("Number the caller gave") as HTMLInputElement).value, "+447400111222");
});

test("changing notes and lead type sends ONE patch with exactly those fields, then onSaved", async () => {
    const { user, patches, corrections, saved } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("combobox", { name: /lead type for/i }));
    await user.click(screen.getByRole("option", { name: "Cold" }));
    const notes = screen.getByLabelText("Notes");
    await user.clear(notes);
    await user.type(notes, "Not interested");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.equal(saved(), 1));
    assert.deepEqual(patches, [{ leadType: "cold", notes: "Not interested" }]);
    assert.deepEqual(corrections, []);
    assert.equal(screen.queryByRole("button", { name: "Edit" }) !== null, true);
});

test("changing one follow-up field sends the whole form with that field changed", async () => {
    const { user, patches } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const next = screen.getByLabelText("Next step");
    await user.clear(next);
    await user.type(next, "Email the quote");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.equal(patches.length, 1));
    assert.deepEqual(patches[0], {
        form: { contact: "Sam", interest: "Plan upgrade", nextStep: "Email the quote", completed: false },
    });
});

test("a bad email shows the sentence and nothing at all is sent", async () => {
    const { user, patches, corrections, saved } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.type(screen.getByLabelText("Notes"), " more");
    const email = screen.getByLabelText("Email the caller gave");
    await user.clear(email);
    await user.type(email, "not-an-email");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.equal(screen.queryByText("Enter a full email address, such as name@example.com.") !== null, true));
    assert.equal(patches.length, 0);
    assert.equal(corrections.length, 0);
    assert.equal(saved(), 0);
});

test("a changed number calls the correction with (call id, phone, value)", async () => {
    const { user, corrections, patches, saved } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const phone = screen.getByLabelText("Number the caller gave");
    await user.clear(phone);
    await user.type(phone, "+44 7400 999888");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.equal(saved(), 1));
    assert.deepEqual(corrections, [["call-1", "phone", "+44 7400 999888"]]);
    assert.equal(patches.length, 0);
});

test("emptying the email sends an empty value, which removes it", async () => {
    const { user, corrections, saved } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.clear(screen.getByLabelText("Email the caller gave"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.equal(saved(), 1));
    assert.deepEqual(corrections, [["call-1", "email", ""]]);
});

test("a failing correction keeps edit mode and the typed values, and says which part saved", async () => {
    const { user, patches, saved } = setup({
        correctFn: async () => { throw new Error("The server refused it."); },
    });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.type(screen.getByLabelText("Notes"), " extra");
    const email = screen.getByLabelText("Email the caller gave");
    await user.clear(email);
    await user.type(email, "new@example.com");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => assert.equal(screen.queryByText("Not saved. Try again.") !== null, true));
    assert.equal(screen.queryByText(/Not saved: the email the caller gave\. The server refused it\./) !== null, true);
    assert.equal(screen.queryByText("Saved: the notes and follow-up.") !== null, true);
    assert.equal((screen.getByLabelText("Email the caller gave") as HTMLInputElement).value, "new@example.com");
    assert.equal((screen.getByLabelText("Notes") as HTMLTextAreaElement).value, "Wants a quote extra");
    assert.equal(screen.queryByRole("button", { name: "Save" }) !== null, true);
    assert.equal(patches.length, 1);
    assert.equal(saved(), 1);
});

test("Cancel restores the saved values and sends nothing", async () => {
    const { user, patches, corrections } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.type(screen.getByLabelText("Notes"), " typed");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    assert.equal(screen.queryByText("Wants a quote") !== null, true);
    await user.click(screen.getByRole("button", { name: "Edit" }));
    assert.equal((screen.getByLabelText("Notes") as HTMLTextAreaElement).value, "Wants a quote");
    assert.equal(patches.length + corrections.length, 0);
});

test("Esc cancels edit mode", async () => {
    const { user, patches } = setup();
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.type(screen.getByLabelText("Notes"), " typed");
    await user.keyboard("{Escape}");
    await waitFor(() => assert.equal(screen.queryByRole("button", { name: "Save" }) === null, true));
    assert.equal(screen.queryByText("Wants a quote") !== null, true);
    assert.equal(patches.length, 0);
});

test("a failed sync shows the banner and Retry sync calls onRetry", async () => {
    const { user, retried } = setup({ failed: true });
    assert.equal(screen.queryByRole("alert") !== null, true);
    await user.click(screen.getByRole("button", { name: "Retry sync" }));
    assert.equal(retried(), 1);
});

test("a sync in progress shows the syncing line", () => {
    setup({ busy: true });
    assert.equal(screen.queryByText("Syncing call details…") !== null, true);
});
