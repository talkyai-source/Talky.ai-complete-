/**
 * After a save or a CSV import that finishes late, the table reloads for the view
 * the customer is looking at NOW (current search / campaign), not the one that was
 * on screen when the save or import began. Also: no frame shows the new page-1
 * header over the previous page's rows.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import { BAD_NUMBER, UK_E164, UK_NATIONAL, chooseUnitedKingdom, csvFile } from "@/test-utils/phone-fixtures";
import { fillContactPermission } from "@/test-utils/contact-permission";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
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

const CAMPAIGNS = { campaigns: [
    { id: "camp-1", name: "Spring push", direction: "outbound" },
    { id: "camp-2", name: "Autumn push", direction: "outbound" },
] };
type Opts = { search?: string; listId?: string | null };
const OLD_ROW = "+447400100001";
const SEARCH_ROW = "+447499900001";
const CAMP2_ROW = "+447411100001";
const TOTAL = 120;
const bigPage = (page: number, size: number) => {
    const start = (page - 1) * size;
    const count = Math.max(0, Math.min(size, TOTAL - start));
    return Array.from({ length: count }, (_, i) => ({ id: `c-${start + i}`, campaign_id: "camp-1", phone_number: `+4474001${String(start + i).padStart(5, "0")}`, status: "pending" }));
};
const one = (id: string, campaign: string, phone: string) => ({ items: [{ id, campaign_id: campaign, phone_number: phone, status: "pending" }], total: 1 });

function mockApis() {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    return mock.method(dashboardApi, "listContacts", async (campaignId: string, _page: number, _size: number, opts?: Opts) => {
        if (campaignId === "camp-2") return one("d-1", "camp-2", CAMP2_ROW);
        if (opts?.search) return one("s-1", "camp-1", SEARCH_ROW);
        return one("o-1", "camp-1", OLD_ROW);
    });
}
const lastRequest = (read: ReturnType<typeof mockApis>) => read.mock.calls.at(-1)!.arguments as unknown as [string, number, number, Opts];
const onScreen = (phone: string) => screen.queryAllByText(phone).length > 0;

test("a save that finishes after the search changed reloads for the NEW search", async () => {
    const read = mockApis();
    let finishSave: (v: unknown) => void = () => {};
    mock.method(dashboardApi, "addContact", () => new Promise((resolve) => { finishSave = resolve; }));
    render(<Page />);
    await waitFor(() => assert.equal(onScreen(OLD_ROW), true), { timeout: SLOW });

    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    const phone = await screen.findByLabelText(/Phone Number/, undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("contact-phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    // While the save is running the customer searches.
    fireEvent.change(screen.getByLabelText("Search contacts by phone number"), { target: { value: "7499" } });
    await waitFor(() => assert.equal(onScreen(SEARCH_ROW), true), { timeout: SLOW });

    const before = read.mock.callCount();
    finishSave({ message: "ok", contact: { id: "c-new", phone_number: UK_E164 } });
    await waitFor(() => assert.ok(read.mock.callCount() > before, "the save reloaded the table"), { timeout: SLOW });
    await waitFor(() => assert.equal(onScreen(SEARCH_ROW), true), { timeout: SLOW });
    const last = lastRequest(read);
    assert.equal(last[1], 1, "the reload asked for page 1");
    assert.equal(last[3].search, "7499", "the reload carries the new search");
    assert.equal(onScreen(OLD_ROW), false, "the old search's rows are not on screen");
});

test("an import that finishes after the campaign changed reloads for the NEW campaign", async () => {
    const read = mockApis();
    let finishImport: (v: unknown) => void = () => {};
    const upload = mock.method(extendedApi, "uploadCSV", () => new Promise((resolve) => { finishImport = resolve; }));
    render(<Page />);
    await waitFor(() => assert.equal(onScreen(OLD_ROW), true), { timeout: SLOW });

    fireEvent.click(await screen.findByRole("button", { name: /Add more contacts/ }, { timeout: SLOW }));
    await screen.findByText("Add more contacts", { selector: "h3" }, { timeout: SLOW });
    const text = ["Phone,First Name", `${UK_NATIONAL},Ann`, `${BAD_NUMBER},Bad`].join("\n");
    fireEvent.change(document.getElementById("contacts-csv-file") as HTMLInputElement, { target: { files: [csvFile(text)] } });
    await screen.findAllByTestId("contacts-preview-row", undefined, { timeout: SLOW });
    await chooseUnitedKingdom("contacts-import-country");
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Upload 1 contact$/ })), { timeout: SLOW });
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: /Upload 1 contact$/ }));
    await waitFor(() => assert.equal(upload.mock.callCount(), 1), { timeout: SLOW });

    // While the import is running the customer picks another campaign.
    fireEvent.click(screen.getByRole("combobox", { name: "Select campaign" }));
    fireEvent.click(await screen.findByRole("option", { name: "Autumn push" }));
    await waitFor(() => assert.equal(onScreen(CAMP2_ROW), true), { timeout: SLOW });

    const before = read.mock.callCount();
    finishImport({ total_rows: 1, imported: 1, failed: 0, duplicates_skipped: 0, errors: [] });
    await waitFor(() => assert.ok(read.mock.callCount() > before, "the import reloaded the table"), { timeout: SLOW });
    await waitFor(() => assert.equal(onScreen(CAMP2_ROW), true), { timeout: SLOW });
    assert.equal(lastRequest(read)[0], "camp-2", "the reload asked for the new campaign");
    assert.equal(onScreen(OLD_ROW), false, "the old campaign's contacts are not under the new campaign");
});

test("changing the search on page 2 never shows the page-1 header over page-2 rows", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [CAMPAIGNS.campaigns[0]] }));
    mock.method(dashboardApi, "listContacts", async (_id: string, page: number, size: number) => ({ items: bigPage(page, size), total: TOTAL, page, page_size: size }));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Next" }, { timeout: SLOW }));
    await waitFor(() => assert.equal(screen.queryAllByText("Showing 51 to 100 of 120 contacts").length > 0, true), { timeout: SLOW });

    const wrong: string[] = [];
    const watch = () => {
        if (screen.queryAllByText("Showing 1 to 50 of 120 contacts").length > 0 && screen.queryAllByText("+447400100050").length > 0) wrong.push("page-1 header over page-2 rows");
    };
    const observer = new window.MutationObserver(watch);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    try {
        fireEvent.change(screen.getByLabelText("Search contacts by phone number"), { target: { value: "7400" } });
        await waitFor(() => {
            assert.equal(screen.queryAllByText("Showing 1 to 50 of 120 contacts").length > 0, true);
            assert.equal(screen.queryAllByText("+447400100050").length, 0);
        }, { timeout: SLOW });
    } finally {
        observer.disconnect();
    }
    assert.deepEqual(wrong, []);
});
