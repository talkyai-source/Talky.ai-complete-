import assert from "node:assert/strict";
import test from "node:test";

import { cleanup, render, screen } from "@testing-library/react";

import {
    CallBillingPanel,
    CallLoadError,
    CallPartiesPanel,
    InboundConsentStatePanel,
    InboundRouteSnapshot,
    TransferLegsPanel,
} from "@/components/calls/call-panels";
import type { TransferLeg } from "@/lib/dashboard-api";

test.afterEach(cleanup);

test("a failed transfer leg is visible with the server's status and reason", () => {
    // Shape and key names are the server's own projection —
    // backend/app/api/v1/endpoints/calls.py:1329-1337.
    const leg: TransferLeg = {
        id: "leg-1",
        leg_type: "transfer",
        status: "failed",
        to_number: "+14155550199",
        duration_seconds: 0,
        metadata: { terminal_reason: "provider_target_absent" },
    };

    render(<TransferLegsPanel legs={[leg]} />);

    assert.ok(screen.getByRole("heading", { name: "Transfer legs" }));
    assert.ok(screen.getByText("Failed"));
    assert.ok(screen.getByText("+14155550199"));
    assert.ok(screen.getByText("provider_target_absent"));
});

test("a held transfer leg shows the reconciliation status rather than a raw token", () => {
    // backend/app/domain/services/telephony/inbound_transfer.py:1544-1556
    render(<TransferLegsPanel legs={[{ id: "leg-2", status: "reconciliation_required" }]} />);
    assert.ok(screen.getByText("Awaiting reconciliation"));
    assert.equal(screen.queryByText("reconciliation_required"), null);
});

test("an undeclared leg status is shown verbatim, never hidden or guessed at", () => {
    render(<TransferLegsPanel legs={[{ id: "leg-3", status: "some_status_added_later" }]} />);
    assert.ok(screen.getByText("some_status_added_later"));
});

test("nothing renders when the server sent no transfer legs", () => {
    const { container: absent } = render(<TransferLegsPanel legs={undefined} />);
    assert.equal(absent.innerHTML, "");
    cleanup();

    const { container: nulled } = render(<TransferLegsPanel legs={null} />);
    assert.equal(nulled.innerHTML, "");
    cleanup();

    // The ordinary case for every call that was never transferred, and for
    // every call at all while the controlled transfer runtime is closed
    // (backend/app/domain/services/telephony/inbound_transfer.py:25).
    const { container: empty } = render(<TransferLegsPanel legs={[]} />);
    assert.equal(empty.innerHTML, "");
});

test("a leg's null fields render nothing at all — no placeholder, no invented copy", () => {
    render(<TransferLegsPanel legs={[{ id: "leg-4", status: "failed", to_number: null, duration_seconds: null, answered_at: null, ended_at: null, metadata: null }]} />);

    assert.ok(screen.getByText("Failed"));
    assert.equal(screen.queryByText("Unknown"), null);
    assert.equal(screen.queryByText("--"), null);
    assert.equal(screen.queryByText(/^Answered /), null);
    assert.equal(screen.queryByText(/^Ended /), null);
});

test("every leg the server sent is listed, including a still-live one", () => {
    render(<TransferLegsPanel legs={[
        { id: "leg-5", status: "answered", to_number: "+14155550101", duration_seconds: 65 },
        { id: "leg-6", status: "failed", to_number: "+14155550102" },
    ]} />);

    assert.equal(screen.getAllByRole("listitem").length, 2);
    assert.ok(screen.getByText("Answered"));
    assert.ok(screen.getByText("Failed"));
    assert.ok(screen.getByText("1:05"));
});

test("a failed call load offers a retry that re-runs the request", () => {
    let retried = 0;
    render(<CallLoadError message="Failed to load call details" onRetry={() => { retried += 1; }} />);

    assert.ok(screen.getByRole("alert"));
    assert.ok(screen.getByText("Failed to load call details"));
    screen.getByRole("button", { name: "Try again" }).click();
    assert.equal(retried, 1);
});

test("a failed load offers no retry control when the caller cannot re-run it", () => {
    render(<CallLoadError message="Failed to load call details" />);

    assert.ok(screen.getByText("Failed to load call details"));
    assert.equal(screen.queryByRole("button", { name: "Try again" }), null);
});

test("the billing panel shows the settled seconds the server sent, in the wire's unit", () => {
    // Field and unit are the server's own projection: billed_duration_seconds
    // is read off the usage ledger, not off duration —
    // backend/app/api/v1/endpoints/calls.py:263, :1306-1312.
    render(
        <CallBillingPanel
            billingStatus="finalized"
            billingHoldReason={null}
            billedDurationSeconds={1830}
        />,
    );
    assert.ok(screen.getByText("Settled"));
    assert.ok(screen.getByText("1,830"));
    assert.ok(screen.getByText("seconds"));
});

test("a held call shows why settlement was withheld", () => {
    render(
        <CallBillingPanel
            billingStatus="held"
            billingHoldReason="usage_exceeded_reservation"
            billedDurationSeconds={null}
        />,
    );
    assert.ok(screen.getByText("Held for review"));
    assert.ok(screen.getByText("Call ran longer than the minutes reserved for it"));
});

test("an undeclared billing value reaches the operator verbatim", () => {
    render(
        <CallBillingPanel billingStatus="adjudicating" billingHoldReason="carrier_dispute" />,
    );
    assert.ok(screen.getByText("adjudicating"));
    assert.ok(screen.getByText("carrier_dispute"));
});

test("a call the server has not settled shows no billed figure at all", () => {
    // An unsettled call has no billed quantity. Falling back to its duration,
    // or printing a placeholder, would assert a charge the server never made.
    render(
        <CallBillingPanel
            billingStatus="reserved"
            billingHoldReason={null}
            billedDurationSeconds={null}
        />,
    );
    assert.ok(screen.getByText("Reserved"));
    assert.equal(screen.queryByText("Billed duration"), null);
    assert.equal(screen.queryByText("seconds"), null);
});

test("nothing renders when the server sent no billing fields", () => {
    const { container } = render(<CallBillingPanel />);
    assert.equal(container.textContent, "");
});

test("a billed zero is a real settled figure and is shown, not swallowed", () => {
    // A released call settles at zero seconds
    // (backend/app/domain/services/telephony/inbound_admission.py:1571-1573).
    // Zero is falsy, so it must not be treated as "the server sent nothing".
    render(<CallBillingPanel billingStatus="released" billedDurationSeconds={0} />);
    assert.ok(screen.getByText("Billed duration"));
    assert.ok(screen.getByText("0"));
});

// ── CallPartiesPanel — caller and DID render exactly as the wire sent them,
// never gated on a capability (backend/app/api/v1/endpoints/calls.py:183, :201-202) ──

test("an inbound call shows Caller ANI and the Called DID the wire sent", () => {
    render(<CallPartiesPanel direction="inbound" phoneNumber="+14155550100" toNumber="+18005550101" />);
    assert.ok(screen.getByText("Caller ANI"));
    assert.ok(screen.getByText("+14155550100"));
    assert.ok(screen.getByText("Called DID"));
    assert.ok(screen.getByText("+18005550101"));
});

test("an outbound call shows Phone Number, never a Called DID row", () => {
    render(<CallPartiesPanel direction="outbound" phoneNumber="+14155550100" toNumber="+18005550101" />);
    assert.ok(screen.getByText("Phone Number"));
    assert.equal(screen.queryByText("Caller ANI"), null);
    assert.equal(screen.queryByText("Called DID"), null);
});

// ── InboundRouteSnapshot — routing detail gated the same as /inbound-campaigns;
// the campaign link is the id itself, since the wire carries no separate name ──

test("the route snapshot renders nothing without inbound:read/inbound:manage, even for an inbound call", () => {
    const { container } = render(
        <InboundRouteSnapshot canView={false} inboundCampaignId="cfg-1" assignmentId="asn-1" routeId="rt-1" routeVersion={2} configVersion={3} configChecksum="abc123" />,
    );
    assert.equal(container.innerHTML, "");
});

test("the route snapshot renders the fields the server sent once the capability is granted", () => {
    render(
        <InboundRouteSnapshot canView inboundCampaignId="cfg-1" assignmentId="asn-1" routeId="rt-1" routeVersion={2} configVersion={3} configChecksum="abcdefghijklmnop" />,
    );
    assert.ok(screen.getByRole("heading", { name: "Inbound route snapshot" }));
    assert.ok(screen.getByText("cfg-1"));
    assert.ok(screen.getByText("asn-1"));
    assert.ok(screen.getByText("rt-1"));
    assert.ok(screen.getByText("2"));
    assert.ok(screen.getByText("3"));
});

test("the campaign link renders only when the wire carries an inbound campaign id", () => {
    render(<InboundRouteSnapshot canView inboundCampaignId="cfg-42" />);
    const link = screen.getByRole("link", { name: "Open inbound campaign" });
    assert.equal(link.getAttribute("href"), "/inbound-campaigns/cfg-42");
});

test("no campaign link renders without an id", () => {
    render(<InboundRouteSnapshot canView inboundCampaignId={null} />);
    assert.equal(screen.queryByRole("link", { name: "Open inbound campaign" }), null);
});

// ── InboundConsentStatePanel — gated the same as the route snapshot ──

test("the consent and media panel renders nothing without the capability", () => {
    const { container } = render(<InboundConsentStatePanel canView={false} admissionStatus="admitted" consentStatus="granted" />);
    assert.equal(container.innerHTML, "");
});

test("nothing renders when the server sent none of the consent or media fields", () => {
    const { container } = render(<InboundConsentStatePanel canView />);
    assert.equal(container.innerHTML, "");
});

test("the consent and media panel renders the fields the server sent, capability granted", () => {
    render(
        <InboundConsentStatePanel
            canView
            admissionStatus="admitted"
            consentStatus="granted"
            processingStatus="live"
            mediaState="connected"
            recordingStatus="recording"
            transcriptStatus="streaming"
            admissionReason="within business hours"
        />,
    );
    assert.ok(screen.getByRole("heading", { name: "Consent and media state" }));
    assert.ok(screen.getByText("admitted"));
    assert.ok(screen.getByText("granted"));
    assert.ok(screen.getByText("within business hours"));
});
