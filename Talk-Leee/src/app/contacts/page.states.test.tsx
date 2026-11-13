/**
 * FX1a Task 3 — Contacts tells the truth: unexpected answers are errors (never
 * a crash), a failed campaign-list or contacts read shows the shared
 * ErrorState with Retry, and the import totals agree with the rows listed.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi, type BulkImportResponse } from "@/lib/extended-api";
import { BAD_NUMBER, UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";
import ContactsError from "./error";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });

const CAMPAIGNS = { campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] };
const CONTACT = { id: "c-1", campaign_id: "camp-1", phone_number: "+447400123456", status: "pending" };
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const retryButton = () => screen.findByRole("button", { name: "Retry" }, { timeout: SLOW });
const contactListed = () => screen.queryAllByText("+447400123456").length > 0;

test("contacts answer {items: null, total: 3} is shown as an error with Retry, not a crash", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    let answer: unknown = { items: null, total: 3 };
    mock.method(dashboardApi, "listContacts", async () => answer);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown("Invalid response format"), false, "the app's technical sentence is not shown");
    assert.equal(shown("Something went wrong"), true);
    assert.equal(shown("Add a contact manually"), false, "the error is outside the empty-campaign layout");
    answer = { items: [CONTACT], total: 1 };
    fireEvent.click(await retryButton());
    await waitFor(() => assert.equal(contactListed(), true), { timeout: SLOW });
    assert.equal(shown("Please retry."), false);
});

const jsError = () => { try { (undefined as unknown as unknown[]).map(() => null); } catch (err) { return err as Error; } throw new Error("expected a TypeError"); };

test("FX2b3: a campaign-list read that fails with a JavaScript error shows 'Please retry.', never the raw text", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => { throw jsError(); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    render(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/Cannot read properties/), false);
});

test("FX2b3: a contacts read that fails with a JavaScript error shows 'Please retry.', never the raw text", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    mock.method(dashboardApi, "listContacts", async () => { throw jsError(); });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown(/Cannot read properties/), false);
});

test("an unexpected campaign-list answer is shown as an error, not a crash and not 'Create a campaign first'", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: null }));
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    render(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown("Invalid response format"), false, "the app's technical sentence is not shown");
    assert.equal((await retryButton()) !== null, true);
    assert.equal(shown("Create a campaign first"), false);
});

test("a failed campaign-list read shows the server's message with Retry, never 'Create a campaign first'", async () => {
    let fail = true;
    const list = mock.method(dashboardApi, "listCampaigns", async () => {
        if (fail) throw new Error("Campaign service is unavailable.");
        return CAMPAIGNS;
    });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [CONTACT], total: 1 }));
    render(<Page />);
    await waitFor(() => assert.equal(shown("Campaign service is unavailable."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true);
    assert.equal(shown("Create a campaign first"), false);
    fail = false;
    fireEvent.click(await retryButton());
    await waitFor(() => assert.equal(contactListed(), true), { timeout: SLOW });
    assert.equal(list.mock.callCount(), 2);
    assert.equal(shown("Campaign service is unavailable."), false);
});

test("a failed contacts read shows the shared ErrorState with Retry outside the empty-campaign layout", async () => {
    let fail = true;
    const read = mock.method(dashboardApi, "listContacts", async () => {
        if (fail) throw new Error("Contacts could not be loaded right now.");
        return { items: [CONTACT], total: 1 };
    });
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    render(<Page />);
    await waitFor(() => assert.equal(shown("Contacts could not be loaded right now."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true);
    assert.equal(shown("Add a contact manually"), false, "no empty-campaign layout around the error");
    assert.equal(shown("Upload CSV"), false);
    fail = false;
    fireEvent.click(await retryButton());
    await waitFor(() => assert.equal(contactListed(), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
});

test("after an import the totals agree with the rows listed: browser-refused plus server-refused", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 1 }));
    const response: BulkImportResponse = {
        total_rows: 2, imported: 1, failed: 1, duplicates_skipped: 0,
        errors: [{ row: 3, error: "Number is on the do-not-call list", phone: "+447400123457" }],
    };
    mock.method(extendedApi, "uploadCSV", async () => response);
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add more contacts/ }, { timeout: SLOW }));
    await screen.findByText("Add more contacts", { selector: "h3" }, { timeout: SLOW });
    const text = ["Phone,First Name", `${UK_NATIONAL},Ann`, "07400 123457,Bob", `${BAD_NUMBER},Bad`].join("\n");
    fireEvent.change(document.getElementById("contacts-csv-file") as HTMLInputElement, { target: { files: [csvFile(text)] } });
    await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-import-country");
    fireEvent.click(await screen.findByRole("button", { name: /Upload 2 contacts/ }, { timeout: SLOW }));
    const results = await screen.findByTestId("contacts-import-results", undefined, { timeout: SLOW });
    const listed = within(results).getAllByTestId("contacts-import-error-row").length;
    assert.equal(listed, 2, "one row refused in the browser and one refused by the server");
    const total = (label: string) => within(results).getByText(label).previousElementSibling?.textContent;
    assert.equal(total("Failed"), String(listed));
    assert.equal(total("Total Rows"), "3");
    assert.equal(total("Imported"), "1");
    assert.equal(total("Duplicates"), "0");
});

test("/contacts has an error page that shows the error with Retry", async () => {
    let resets = 0;
    render(<ContactsError error={new Error("Rendering failed.")} reset={() => { resets++; }} />);
    assert.equal(shown("Rendering failed."), true);
    fireEvent.click(await retryButton());
    assert.equal(resets, 1);
});
