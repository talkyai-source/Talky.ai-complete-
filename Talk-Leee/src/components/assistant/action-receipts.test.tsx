import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AssistantRunStatusSchema } from "@/lib/models";
import { EditProposalCard, withProposalResult, type ProposalData } from "./edit-proposal-card";

afterEach(cleanup);

test("all actual durable action states remain distinct in the frontend", () => {
    for (const status of ["pending", "running", "scheduled", "completed", "failed", "unknown", "cancelled"]) {
        assert.equal(AssistantRunStatusSchema.parse(status), status);
    }
});

test("an unknown provider result stays unresolved without an Apply or retry button", () => {
    const proposal = {
        proposalId: "proposal-unknown", tool: "send_email", changes: [],
        status: "unknown", actionId: "action-owned-1", error: "The provider response was lost.",
    } as unknown as ProposalData;
    render(<EditProposalCard proposal={proposal} onApply={() => assert.fail("must not reapply")} onReject={() => {}} />);
    assert.ok(screen.getByText("Outcome unknown"));
    assert.ok(screen.getByText(/Check the saved action record before sending again/i));
    assert.ok(screen.getByText(/action-owned-1/));
    assert.equal(screen.queryByRole("button", { name: /apply|retry/i }), null);
});

test("provider-accepted email is not presented as delivered", () => {
    const proposal = {
        proposalId: "proposal-email", tool: "send_email", changes: [], status: "applied",
        actionId: "action-owned-2", actionStatus: "completed", confirmationAllowed: true,
        receipt: { provider: "gmail", message_id: "provider-message-2", provider_status: "accepted" },
    } as unknown as ProposalData;
    render(<EditProposalCard proposal={proposal} onApply={() => {}} onReject={() => {}} />);
    assert.ok(screen.getByText("Accepted by email provider"));
    assert.equal(screen.queryByText(/^Delivered$/i), null);
    assert.ok(screen.getByText(/provider-message-2/));
});

test("scheduled callback is not presented as a completed call", () => {
    const proposal = {
        proposalId: "proposal-callback", tool: "request_callback", changes: [],
        status: "scheduled", actionId: "action-owned-3", actionStatus: "scheduled",
    } as unknown as ProposalData;
    render(<EditProposalCard proposal={proposal} onApply={() => {}} onReject={() => {}} />);
    assert.ok(screen.getByText("Scheduled"));
    assert.equal(screen.queryByText(/^Completed$|^Applied$/), null);
});

test("unknown receipt state takes precedence over an optimistic applied flag", () => {
    const draft: ProposalData = { proposalId: "same-proposal", tool: "send_email", status: "submitting" };
    const result = withProposalResult(draft, { status: "unknown", applied: true, action_id: "same-action", confirmation_allowed: false });
    assert.equal(result.status, "unknown");
    assert.equal(result.actionId, "same-action");
    assert.equal(result.confirmationAllowed, false);
});

test("status lookup uses the same proposal and cannot invoke Apply", () => {
    const checked: string[] = [];
    render(<EditProposalCard proposal={{ proposalId: "same-proposal", tool: "send_email", status: "unknown" }}
        onApply={() => assert.fail("lookup cannot execute")}
        onReject={() => {}} onCheck={(id) => checked.push(id)} />);
    fireEvent.click(screen.getByRole("button", { name: "Check status" }));
    assert.deepEqual(checked, ["same-proposal"]);
});

test("a missing receipt after a lost response stays uncertain without inviting another submission", () => {
    const result = withProposalResult({ proposalId: "same-proposal", tool: "send_email", status: "submitting" },
        { status: "unavailable", applied: false, confirmation_allowed: false, error: "No saved result is available." });
    assert.equal(result.status, "unknown");
    assert.equal(result.confirmationAllowed, false);
});

test("a saved reminder keeps its reference and scheduled status without claiming delivery", () => {
    const result = withProposalResult({ proposalId: "reminder-proposal", tool: "schedule_reminder", status: "submitting" },
        { status: "scheduled", applied: true, confirmation_allowed: true,
            receipt: { reminder_id: "saved-reminder", provider_status: "scheduled", secret: "must-not-render" } });
    render(<EditProposalCard proposal={result} onApply={() => assert.fail("must not schedule twice")} onReject={() => {}} />);
    assert.ok(screen.getByText("Scheduled"));
    assert.ok(screen.getByText(/saved-reminder/));
    assert.equal(screen.queryByText(/must-not-render|Delivered/), null);
    assert.equal(screen.queryByRole("button", { name: /^Apply$/ }), null);
});

const HELD_BACK_NOTICE = "No contact was queued. 3 contacts were not queued because no permission to call is recorded.";

test("an applied result with a notice shows Applied and the notice", () => {
    const result = withProposalResult({ proposalId: "start-proposal", tool: "start_campaign", status: "submitting" },
        { status: "completed", applied: true, confirmation_allowed: true, notice: HELD_BACK_NOTICE });
    assert.equal(result.notice, HELD_BACK_NOTICE);
    render(<EditProposalCard proposal={result} onApply={() => {}} onReject={() => {}} />);
    assert.ok(screen.getByText("Applied"));
    assert.ok(screen.getByText(HELD_BACK_NOTICE));
});

test("an applied result without a notice renders no notice", () => {
    const result = withProposalResult({ proposalId: "start-proposal", tool: "start_campaign", status: "submitting" },
        { status: "completed", applied: true, confirmation_allowed: true });
    assert.equal(result.notice, undefined);
    const { container } = render(<EditProposalCard proposal={result} onApply={() => {}} onReject={() => {}} />);
    assert.ok(screen.getByText("Applied"));
    assert.equal(container.querySelector("p.text-amber-700"), null);
});

test("a result that is not applied ignores a notice", () => {
    const result = withProposalResult({ proposalId: "start-proposal", tool: "start_campaign", status: "submitting" },
        { status: "failed", applied: false, confirmation_allowed: false, error: "Could not start.", notice: HELD_BACK_NOTICE });
    assert.equal(result.notice, undefined);
    render(<EditProposalCard proposal={result} onApply={() => {}} onReject={() => {}} />);
    assert.equal(screen.queryByText(HELD_BACK_NOTICE), null);
});

test("a workflow receipt keeps the saved plan reference while incomplete proof stays unknown", () => {
    const result = withProposalResult({ proposalId: "plan-proposal", tool: "execute_action_plan", status: "submitting" },
        { status: "completed", applied: true, confirmation_allowed: false, receipt: { plan_id: "saved-plan" } });
    render(<EditProposalCard proposal={result} onApply={() => assert.fail("must not execute twice")} onReject={() => {}} />);
    assert.ok(screen.getByText("Outcome unknown"));
    assert.ok(screen.getByText(/saved-plan/));
    assert.equal(screen.queryByRole("button", { name: /^Apply$/ }), null);
});
