import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { ContactLists } from "@/components/campaigns/contact-lists";
import { dashboardApi, type ContactList } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";

const originalList = dashboardApi.listContactLists;
const originalCall = dashboardApi.callContactList;
const originalConfirm = window.confirm;

const inactiveList: ContactList = {
    id: "list-1",
    name: "Priority leads",
    source: "csv",
    is_active: false,
    contact_count: 3,
    created_at: "2026-09-03T00:00:00Z",
};

afterEach(() => {
    cleanup();
    dashboardApi.listContactLists = originalList;
    dashboardApi.callContactList = originalCall;
    window.confirm = originalConfirm;
});

test("call-list success applies the server's authoritative active state without a parent callback", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    dashboardApi.listContactLists = async () => [inactiveList];
    dashboardApi.callContactList = async () => ({
        list_id: inactiveList.id,
        is_active: true,
        eligible_count: 3,
        jobs_enqueued: 3,
        started: true,
        message: "Calls queued.",
    });
    window.confirm = () => true;

    render(<ContactLists campaignId="campaign-a" />);
    assert.ok(await screen.findByText("Inactive"));

    await user.click(screen.getByRole("button", { name: "Call this list" }));

    await waitFor(() => assert.ok(screen.getByText("Active")));
    assert.equal(screen.queryByText("Inactive"), null);
});

test("partial 503 applies is_active from structured server details", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    dashboardApi.listContactLists = async () => [inactiveList];
    dashboardApi.callContactList = async () => {
        throw new ApiClientError({
            status: 503,
            code: "contact_list_start_failed_after_activation",
            message: "The list was activated, but its contacts were not queued.",
            details: {
                list_id: inactiveList.id,
                campaign_id: "campaign-a",
                is_active: true,
                jobs_enqueued: 0,
            },
            method: "POST",
            url: `/contact-lists/${inactiveList.id}/call`,
        });
    };
    window.confirm = () => true;

    render(<ContactLists campaignId="campaign-a" />);
    assert.ok(await screen.findByText("Inactive"));

    await user.click(screen.getByRole("button", { name: "Call this list" }));

    await waitFor(() => assert.ok(screen.getByText("Active")));
    assert.equal(screen.queryByText("Inactive"), null);
});
