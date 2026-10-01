import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LeadDetailsPanel } from "@/components/calls/lead-details-panel";

afterEach(cleanup);

function show(data: unknown) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: 0 } } });
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
