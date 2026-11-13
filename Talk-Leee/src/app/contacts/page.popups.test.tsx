/**
 * FX2a Task 2 — Contacts: the shared loading and empty states; add / edit /
 * delete failures shown inline with the text the browser alert used to show;
 * Remove contact asked in the app's confirm dialog. No alert(), no confirm().
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    // Their side reads effective permissions (QA F1-21); unknown offers every action.
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    if (id === "next/link") {
        return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    }
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
const PHONE = "+447400123456";
const CONFIRM_SENTENCE = `Remove ${PHONE} from this campaign? It will no longer be dialed.`;
const CAMPAIGNS = { campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] };
const CONTACT = { id: "c-1", campaign_id: "camp-1", phone_number: PHONE, first_name: "Ada", last_name: "Byron", status: "pending" };
const shown = (text: string | RegExp) => screen.queryAllByText(text).length > 0;
const originalAlert = window.alert;
const originalConfirm = window.confirm;
let popups = 0;

beforeEach(() => {
    popups = 0;
    window.alert = () => { popups += 1; };
    window.confirm = () => { popups += 1; return true; };
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.alert = originalAlert;
    window.confirm = originalConfirm;
});

function mockPage() {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    mock.method(dashboardApi, "listContacts", async () => ({ items: [CONTACT], total: 1 }));
}
const deleteButtons = () => screen.findAllByRole("button", { name: `Delete ${PHONE}` }, { timeout: SLOW });

test("loading campaigns and loading contacts are announced through the shared loading state", async () => {
    let answerCampaigns!: (value: typeof CAMPAIGNS) => void;
    let answerContacts!: (value: { items: unknown[]; total: number }) => void;
    mock.method(dashboardApi, "listCampaigns", () => new Promise<typeof CAMPAIGNS>((resolve) => { answerCampaigns = resolve; }));
    mock.method(dashboardApi, "listContacts", () => new Promise<{ items: unknown[]; total: number }>((resolve) => { answerContacts = resolve; }));
    render(<Page />);
    assert.equal(within(await screen.findByRole("status")).queryByText("Loading campaigns…") !== null, true);
    await act(async () => { answerCampaigns(CAMPAIGNS); });
    await waitFor(() => assert.equal(shown("Loading contacts…"), true), { timeout: SLOW });
    assert.equal(within(screen.getByRole("status")).queryByText("Loading contacts…") !== null, true);
    await act(async () => { answerContacts({ items: [CONTACT], total: 1 }); });
    await waitFor(() => assert.equal(shown(PHONE), true), { timeout: SLOW });
    assert.equal(shown("Loading contacts…"), false);
});

test("with no campaigns the shared empty state says 'Create a campaign first' and links to Campaigns", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => ({ campaigns: [] }));
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    render(<Page />);
    await waitFor(() => assert.equal(shown("Create a campaign first"), true), { timeout: SLOW });
    assert.equal(shown("Every contact belongs to a campaign so calls, recordings, and results stay organised in the right workspace."), true);
    assert.equal(screen.getByRole("link", { name: "Create a campaign" }).getAttribute("href"), "/campaigns");
    // The shared EmptyState card, not the page's own "content-card" markup.
    assert.equal(screen.getByRole("heading", { name: "Create a campaign first" }).closest(".content-card") === null, true);
});

test("a search that matches nothing shows the shared empty state with the existing sentence", async () => {
    mock.method(dashboardApi, "listCampaigns", async () => CAMPAIGNS);
    let search = false;
    mock.method(dashboardApi, "listContacts", async () => (search ? { items: [], total: 0 } : { items: [CONTACT], total: 1 }));
    render(<Page />);
    await deleteButtons();
    search = true;
    fireEvent.change(screen.getByLabelText("Search contacts by phone number"), { target: { value: "999" } });
    await waitFor(() => assert.equal(shown("No contacts match your search or filter."), true), { timeout: SLOW });
    assert.equal(screen.getByRole("heading", { name: "No contacts match your search or filter." }) !== null, true, "the shared EmptyState title");
});

test("a failed add shows the server's message inline in the form, with no browser alert", async () => {
    mockPage();
    const add = mock.method(dashboardApi, "addContact", async () => { throw new Error("This number is on the do-not-call list"); });
    render(<Page />);
    await deleteButtons();
    fireEvent.click(screen.getByRole("button", { name: "Add contact" }));
    const form = document.getElementById("contact-inline-form") as HTMLFormElement;
    const phone = form.querySelector<HTMLInputElement>('input[type="tel"], input[id*="phone"]')!;
    fireEvent.change(phone, { target: { value: "+447400123457" } });
    const save = within(form).getByRole("button", { name: "Add" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(save.disabled, false), { timeout: SLOW });
    fireEvent.click(save);

    await waitFor(() => assert.equal(within(form).queryByText("This number is on the do-not-call list") !== null, true), { timeout: SLOW });
    assert.equal(within(form).getByText("This number is on the do-not-call list").getAttribute("role"), "alert");
    assert.equal(add.mock.callCount(), 1);
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("a failed edit shows the server's message inline in the form, with no browser alert", async () => {
    mockPage();
    mock.method(dashboardApi, "updateContact", async () => { throw new Error("Contact was changed by someone else"); });
    render(<Page />);
    fireEvent.click((await screen.findAllByRole("button", { name: `Edit ${PHONE}` }, { timeout: SLOW }))[0]!);
    const form = document.getElementById("contact-inline-form") as HTMLFormElement;
    const save = within(form).getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(save.disabled, false), { timeout: SLOW });
    fireEvent.click(save);

    await waitFor(() => assert.equal(within(form).queryByText("Contact was changed by someone else") !== null, true), { timeout: SLOW });
    assert.equal(within(form).getByText("Contact was changed by someone else").getAttribute("role"), "alert");
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("Remove contact asks in the app's confirm dialog; Cancel sends nothing", async () => {
    mockPage();
    const remove = mock.method(dashboardApi, "deleteContact", async () => ({}));
    render(<Page />);
    fireEvent.click((await deleteButtons())[0]!);

    const dialog = within(await screen.findByRole("dialog"));
    assert.equal(dialog.queryByText(CONFIRM_SENTENCE) !== null, true, "the existing confirm sentence is the body");
    assert.equal(dialog.queryAllByText("Remove contact").length, 2, "title and confirm button");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(remove.mock.callCount(), 0);
    assert.equal(shown(PHONE), true, "the contact is still listed");
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("confirming Remove contact deletes the contact", async () => {
    mockPage();
    const remove = mock.method(dashboardApi, "deleteContact", async () => ({}));
    render(<Page />);
    fireEvent.click((await deleteButtons())[0]!);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove contact" }));

    await waitFor(() => assert.equal(remove.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(remove.mock.calls[0]!.arguments, ["camp-1", "c-1"]);
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("a failed delete shows the server's message inline in the contact's row, with no browser alert", async () => {
    mockPage();
    mock.method(dashboardApi, "deleteContact", async () => { throw new Error("Contact is in an active call"); });
    render(<Page />);
    const buttons = await deleteButtons();
    fireEvent.click(buttons[0]!);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove contact" }));

    await waitFor(() => assert.equal(shown("Contact is in an active call"), true), { timeout: SLOW });
    const messages = screen.getAllByText("Contact is in an active call");
    assert.equal(messages.every((node) => node.getAttribute("role") === "alert"), true);
    // The page renders the contact once as a card (small screens) and once as
    // a table row: each carries its own message next to its own Delete button.
    assert.equal(messages.length, buttons.length);
    assert.equal(messages.every((node) => buttons.some((button) => (button.closest("tr") ?? button.closest("article"))!.contains(node))), true, "shown in the row it came from");
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no alert() or confirm()");
});
