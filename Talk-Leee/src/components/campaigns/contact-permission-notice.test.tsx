/**
 * The campaign page notice about contacts with no permission to call on record.
 * An older server has no summary route: nothing is shown then.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { ContactPermissionNotice } from "@/components/campaigns/contact-permission-notice";
import { summaryNotice, serverHasPermissionSummary } from "@/lib/contact-permission-summary";
import { dashboardApi, type ContactPermissionSummary } from "@/lib/dashboard-api";

const original = dashboardApi.getContactPermissionSummary;
afterEach(() => {
    cleanup();
    dashboardApi.getContactPermissionSummary = original;
});

function summary(over: Partial<ContactPermissionSummary>): ContactPermissionSummary {
    return {
        dial_gate: "on", statement_version: "2026-11-v1", waiting_without_permission: 312,
        ungrouped_without_permission: 0, queues_on_confirm: false, ...over,
    };
}

test("mode on: the exact title and body, plural and singular", () => {
    assert.deepEqual(summaryNotice(summary({})), {
        title: "312 contacts will not be called",
        body: "No permission to call is recorded for them. Confirm permission on their contact list below.",
    });
    assert.equal(summaryNotice(summary({ waiting_without_permission: 1 }))?.title, "1 contact will not be called");
});

test("mode off and warn: the softer wording, plural and singular", () => {
    for (const gate of ["off", "warn"] as const) {
        assert.deepEqual(summaryNotice(summary({ dial_gate: gate })), {
            title: "312 contacts have no permission to call on record",
            body: "Confirm permission on their contact list below.",
        });
        assert.equal(summaryNotice(summary({ dial_gate: gate, waiting_without_permission: 1 }))?.title, "1 contact has no permission to call on record");
    }
});

test("no summary (older server) and a count of zero give no notice", () => {
    assert.equal(summaryNotice(null), null);
    assert.equal(summaryNotice(summary({ waiting_without_permission: 0 })), null);
});

test("a newer server that answers with no summary renders nothing", async () => {
    let asked = 0;
    dashboardApi.getContactPermissionSummary = async () => { asked++; return null; };
    const { container } = render(<ContactPermissionNotice campaignId="c1" serverHasSummary refreshKey="a" onShowLists={() => {}} />);
    await waitFor(() => assert.equal(asked, 1));
    assert.equal(container.textContent, "");
});

test("an older server is never asked for the summary, and nothing is rendered", async () => {
    // Asking would fail there and leave an error in the browser's console on every campaign page.
    let asked = 0;
    dashboardApi.getContactPermissionSummary = async () => { asked++; return null; };
    const view = render(<ContactPermissionNotice campaignId="c1" serverHasSummary={false} refreshKey="a" onShowLists={() => {}} />);
    view.rerender(<ContactPermissionNotice campaignId="c1" serverHasSummary={false} refreshKey="b" onShowLists={() => {}} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(asked, 0);
    assert.equal(view.container.textContent, "");
});

test("only a lists answer from the newer server switches the summary on", () => {
    const older = [{ id: "ungrouped", name: "Ungrouped", contact_count: 7 }];
    const newer = [{ id: "ungrouped", name: "Ungrouped", contact_count: 7, queued_for_calling: null, message: null }];
    assert.equal(serverHasPermissionSummary(older), false);
    assert.equal(serverHasPermissionSummary(newer), true);
    assert.equal(serverHasPermissionSummary([]), false);
    assert.equal(serverHasPermissionSummary(null), false);
});

test("the notice shows the words and the button calls onShowLists", async () => {
    dashboardApi.getContactPermissionSummary = async () => summary({});
    let shown = 0;
    render(<ContactPermissionNotice campaignId="c1" serverHasSummary refreshKey="a" onShowLists={() => { shown++; }} />);
    await screen.findByText("312 contacts will not be called");
    assert.equal(screen.getByRole("status").textContent?.includes("No permission to call is recorded for them."), true);
    fireEvent.click(screen.getByRole("button", { name: "Show contact lists" }));
    assert.equal(shown, 1);
});

test("a new refreshKey reads the count again", async () => {
    const calls: string[] = [];
    let count = 312;
    dashboardApi.getContactPermissionSummary = async (id) => { calls.push(id); return summary({ waiting_without_permission: count }); };
    const view = render(<ContactPermissionNotice campaignId="c1" serverHasSummary refreshKey="a" onShowLists={() => {}} />);
    await screen.findByText("312 contacts will not be called");
    count = 5;
    view.rerender(<ContactPermissionNotice campaignId="c1" serverHasSummary refreshKey="b" onShowLists={() => {}} />);
    await screen.findByText("5 contacts will not be called");
    assert.equal(calls.length, 2);
});
