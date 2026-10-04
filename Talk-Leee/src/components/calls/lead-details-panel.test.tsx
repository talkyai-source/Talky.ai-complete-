import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LeadDetailsPanel } from "@/components/calls/lead-details-panel";
import { leadDetailsApi } from "@/lib/lead-details-api";

afterEach(cleanup);

function show(data: unknown) {
    const client = new QueryClient({ defaultOptions: {
        queries: { retry: false, staleTime: Infinity, gcTime: 0 }, mutations: { gcTime: 0 },
    } });
    client.setQueryData(["contact-lead-details", "lead-1"], data);
    render(<QueryClientProvider client={client}><LeadDetailsPanel leadId="lead-1" /></QueryClientProvider>);
}

test("lead notes preserve negation and show review evidence separately from confirmed contacts", () => {
    show({ processing_status: "complete", missing_required: [], details: [{
        call_id: "call-1", field_key: "current_provider", field_type: "notes",
        value: "My brother uses Stripe; I do not.", source: "caller_stated", confirmed: false,
        is_required: false, updated_at: "2026-10-01T00:00:00Z",
        evidence: { source_quote: "My brother uses Stripe; I do not.", status: "needs_review" },
    }] });
    assert.ok(screen.getByText("My brother uses Stripe; I do not."));
    assert.ok(screen.getByText("Needs Review"));
    assert.ok(screen.getByText(/not confirmed by the caller/));
    assert.equal(screen.queryByText("confirmed on the call"), null);
});

test("failed or historical analysis is not presented as a conversation with no captured facts", () => {
    show({ processing_status: "failed", missing_required: [], details: [] });
    assert.ok(screen.getByText(/could not be processed/));
    assert.equal(screen.queryByText(/No supported details/), null);
    cleanup();
    show({ processing_status: "not_processed", missing_required: [], details: [] });
    assert.ok(screen.getByText(/older call has not been analyzed/));
});

test("CRM success and uncertain delivery remain separate for each provider", () => {
    show({ processing_status: "complete", missing_required: [], details: [], crm_deliveries: [
        { provider: "hubspot", status: "succeeded", attempts: 1, updated_at: "2026-10-01" },
        { provider: "salesforce", status: "unknown", attempts: 1, updated_at: "2026-10-01" },
    ] });
    assert.ok(screen.getByText("Hubspot: Synced"));
    assert.ok(screen.getByText(/Salesforce: Outcome uncertain/));
    assert.equal(screen.queryByText("Salesforce: Synced"), null);
});

for (const [status, message] of [
    ["awaiting_confirmation", "Awaiting caller confirmation"],
    ["needs_clarification", "Needs clarification"],
    ["invalid", "Invalid contact — needs correction"],
    ["cancelled", "Withdrawn or replaced — do not use"],
    [undefined, "No usable value was saved"],
] as const) {
    test(`an empty ${status ?? "legacy"} contact does not invent a caller refusal`, () => {
        show({ processing_status: "complete", missing_required: ["email"], details: [{
            call_id: "call-1", field_key: "email", field_type: "email", value: null,
            validation_status: status, source: "caller_stated", confirmed: false,
            is_required: true, updated_at: "2026-10-05T00:00:00Z",
        }] });
        assert.ok(screen.getByText(message));
        assert.equal(screen.queryByText(/caller didn't give one/), null);
        assert.equal(screen.queryByText(/never established on the call/), null);
        assert.equal(screen.queryByText("confirmed on the call"), null);
    });
}

test("a parsed contact is visible without being labeled confirmed", () => {
    show({ processing_status: "complete", missing_required: [], details: [{
        call_id: "call-1", field_key: "email", field_type: "email", value: "alex@example.com",
        validation_status: "awaiting_confirmation", source: "caller_stated", confirmed: false,
        is_required: false, updated_at: "2026-10-05T00:00:00Z",
    }] });
    assert.ok(screen.getByText("alex@example.com"));
    assert.ok(screen.getByText("Awaiting caller confirmation"));
    assert.equal(screen.queryByText("confirmed on the call"), null);
});

test("manual removal is not labeled a verified contact", () => {
    show({ processing_status: "complete", missing_required: [], details: [{
        call_id: "call-1", field_key: "email", field_type: "email", value: null,
        validation_status: "cancelled", source: "manual_edit", confirmed: false,
        is_required: false, updated_at: "2026-10-05T00:00:00Z",
    }] });
    assert.ok(screen.getByText("Withdrawn or replaced — do not use"));
    assert.ok(screen.getByText("Edited by a person"));
    assert.equal(screen.queryByText("Verified by a person"), null);
});

for (const state of ["partial", "failed", "unknown"] as const) {
    test(`completed analysis does not hide ${state} transcript evidence`, () => {
        show({ processing_status: "complete", transcript_save_state: state, missing_required: [], details: [] });
        assert.match(screen.getByRole("status").textContent ?? "", {
            partial: /final transcript save for the latest call is not confirmed/,
            failed: /final transcript for the latest call could not be saved/,
            unknown: /completeness for the latest call has not been verified/,
        }[state]);
        assert.ok(screen.getByText("No captured details are available from the saved evidence."));
    });
}

test("the source revision and confirmation turn are separate evidence", () => {
    show({ processing_status: "complete", transcript_save_state: "complete", missing_required: [], details: [{
        call_id: "call-1", field_key: "email", field_type: "email", value: "alex@example.com",
        validation_status: "confirmed", source: "caller_stated", confirmed: true,
        is_required: false, updated_at: "2026-10-05T00:00:00Z",
        evidence: {
            value_source: { provider_item_id: "caller-4", caller_turn_order: 4, revision_sha256: "a".repeat(64) },
            confirmation_source: { provider_item_id: "caller-6", caller_turn_order: 6, revision_sha256: "b".repeat(64) },
            confirmation_evidence: "readback_and_caller_affirmation",
        },
    }] });
    assert.ok(screen.getByText("confirmed on the call"));
    assert.ok(screen.getByText(/Captured from caller turn 4 · Revision aaaaaaaaaaaa/));
    assert.ok(screen.getByText(/Confirmation from caller turn 6 · Revision bbbbbbbbbbbb/));
    assert.equal(screen.queryByText(/heard by the caller/i), null);
});

test("a contact with no calls does not show a transcript warning", () => {
    show({ processing_status: "no_calls", transcript_save_state: "unknown", missing_required: [], details: [] });
    assert.ok(screen.getByText("No calls recorded for this contact."));
    assert.equal(screen.queryByRole("status"), null);
});

test("incomplete legacy source metadata cannot hide the saved field or fabricate a revision", () => {
    show({ processing_status: "complete", missing_required: [], details: [{
        call_id: "call-1", field_key: "email", field_type: "email", value: "alex@example.com",
        validation_status: "awaiting_confirmation", source: "caller_stated", confirmed: false,
        is_required: false, updated_at: "2026-10-05T00:00:00Z",
        evidence: { value_source: { provider_item_id: "old" }, confirmation_source: {} },
    }] });
    assert.ok(screen.getByText("alex@example.com"));
    assert.equal(screen.queryByText("Source evidence"), null);
});

test("withdrawal evidence does not present the old confirmation as current", () => {
    show({ processing_status: "complete", missing_required: [], details: [{
        call_id: "call-1", field_key: "email", field_type: "email", value: null,
        validation_status: "cancelled", source: "caller_stated", confirmed: false,
        is_required: false, updated_at: "2026-10-05T00:00:00Z",
        evidence: {
            confirmation_source: { provider_item_id: "caller-6", caller_turn_order: 6, revision_sha256: "a".repeat(64) },
            status_source: { provider_item_id: "caller-8", caller_turn_order: 8, revision_sha256: "b".repeat(64) },
        },
    }] });
    assert.ok(screen.getByText("Withdrawn or replaced — do not use"));
    assert.ok(screen.getByText(/Previous confirmation from caller turn 6/));
    assert.ok(screen.getByText(/Status changed by caller turn 8/));
    assert.equal(screen.queryByText("confirmed on the call"), null);
});

test("a failed manual save keeps the original evidence visible and shows the error", async () => {
    const original = leadDetailsApi.correct;
    const writes: unknown[][] = [];
    leadDetailsApi.correct = async (...args) => { writes.push(args); throw new Error("The database is unavailable"); };
    try {
        show({ processing_status: "complete", missing_required: [], details: [{
            call_id: "call-1", field_key: "email", field_type: "email", value: "alex@example.com",
            validation_status: "confirmed", source: "caller_stated", confirmed: true,
            is_required: false, updated_at: "2026-10-05T00:00:00Z",
        }] });
        fireEvent.click(screen.getByRole("button", { name: "Edit Email" }));
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await waitFor(() => assert.equal(screen.getByRole("alert").textContent, "The database is unavailable"));
        assert.deepEqual(writes, [["call-1", "email", null, "email"]]);
        assert.ok(screen.getByRole("textbox"));
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        assert.ok(screen.getByText("alex@example.com"));
        assert.equal(screen.queryByText("Withdrawn or replaced — do not use"), null);
    } finally {
        leadDetailsApi.correct = original;
    }
});
