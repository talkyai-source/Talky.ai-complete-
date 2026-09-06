import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { SalesforceSettingsView, type SalesforceSettingsViewProps } from "@/components/connectors/salesforce-settings";
import type { SalesforceSettingsResponse } from "@/lib/models";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(() => cleanup());

function connectedData(overrides: Partial<SalesforceSettingsResponse> = {}): SalesforceSettingsResponse {
    return {
        server_configured: true,
        connected: true,
        status: "connected",
        connector_id: "conn-1",
        org_id: "00D000000000001EAA",
        username: "ops@acme.com",
        instance_url: "https://acme.my.salesforce.com",
        api_version: "v60.0",
        login_url: "https://login.salesforce.com",
        settings: { callback_campaign_id: "camp-1", log_calls: true, create_leads: true, sync_inbound: true, callback_priority: 8 },
        webhook: {
            callback_url: "https://api.example/api/v1/connectors/salesforce/callback-requests/t/abcd…wxyz",
            outbound_message_url: "https://api.example/api/v1/connectors/salesforce/outbound-message/t/abcd…wxyz",
            token_set: true,
            token_masked: "abcd…wxyz",
            token: null,
        },
        last_synced_call_at: null,
        ...overrides,
    };
}

function renderView(props: Partial<SalesforceSettingsViewProps> = {}) {
    const calls: Record<string, unknown[]> = { save: [], reveal: [], rotate: [], test: [], import: [] };
    const full: SalesforceSettingsViewProps = {
        data: connectedData(),
        campaigns: [
            { id: "camp-1", name: "Spring promo", status: "running" },
            { id: "camp-2", name: "Winter", status: "draft" },
        ],
        onSave: async (input) => {
            calls.save.push(input);
            return undefined;
        },
        onReveal: async () => {
            calls.reveal.push(true);
            return undefined;
        },
        onRotate: async () => {
            calls.rotate.push(true);
            return undefined;
        },
        onTest: async () => {
            calls.test.push(true);
            return { ok: true, username: "ops@acme.com", instance_url: "https://acme.my.salesforce.com" };
        },
        onImport: async (input) => {
            calls.import.push(input);
            return { campaign_id: input.campaign_id, list_id: "list-1", fetched: 3, imported: 2, revived: 0, duplicates_skipped: 1, invalid: 0, errors: [] };
        },
        ...props,
    };
    renderWithQueryClient(createElement(SalesforceSettingsView, full));
    return calls;
}

test("disconnected Salesforce shows guidance instead of settings", () => {
    renderView({ data: connectedData({ connected: false, status: "disconnected" }) });
    assert.ok(screen.getByTestId("salesforce-not-connected"));
    assert.equal(screen.queryByTestId("salesforce-save"), null);
});

test("server without a Connected App explains what an operator must add", () => {
    renderView({ data: connectedData({ connected: false, status: "disconnected", server_configured: false }) });
    assert.match(screen.getByTestId("salesforce-settings").textContent ?? "", /SALESFORCE_CLIENT_ID/);
});

test("connected view shows masked webhook URLs and a Reveal button", () => {
    renderView();
    const url = screen.getByTestId("salesforce-callback-url") as HTMLInputElement;
    assert.match(url.value, /callback-requests\/t\/abcd…wxyz$/);
    assert.ok(screen.getByTestId("salesforce-reveal"));
    assert.equal(screen.getByRole("button", { name: "Rotate token" }).hasAttribute("disabled"), false);
});

test("Save is disabled until a setting changes, then sends only the diff", async () => {
    const calls = renderView();
    const save = screen.getByTestId("salesforce-save") as HTMLButtonElement;
    assert.equal(save.disabled, true);

    fireEvent.click(screen.getByRole("switch", { name: "Log calls as Salesforce Tasks" }));
    assert.equal(save.disabled, false);
    fireEvent.click(save);

    await waitFor(() => assert.equal(calls.save.length, 1));
    assert.deepEqual(calls.save[0], { log_calls: false });
});

test("clearing the callback campaign sends clear_callback_campaign", async () => {
    const calls = renderView();
    // Custom listbox Select: open via its trigger button, then pick the option.
    fireEvent.click(screen.getByRole("button", { name: "Salesforce callback campaign" }));
    fireEvent.click(await screen.findByRole("option", { name: "— Not set —" }));
    fireEvent.click(screen.getByTestId("salesforce-save"));
    await waitFor(() => assert.equal(calls.save.length, 1));
    assert.deepEqual(calls.save[0], { clear_callback_campaign: true });
});

test("Reveal and Test connection call their handlers and show the probe result", async () => {
    const calls = renderView();
    fireEvent.click(screen.getByTestId("salesforce-reveal"));
    fireEvent.click(screen.getByTestId("salesforce-test"));
    await waitFor(() => assert.equal(calls.reveal.length, 1));
    await waitFor(() => assert.ok(screen.getByTestId("salesforce-probe")));
    assert.match(screen.getByTestId("salesforce-probe").textContent ?? "", /OK — ops@acme.com/);
});

test("Import sends the chosen object, campaign and clamped limit and shows counts", async () => {
    const calls = renderView();
    fireEvent.change(screen.getByLabelText("Max records"), { target: { value: "99999" } });
    fireEvent.click(screen.getByTestId("salesforce-import"));
    await waitFor(() => assert.equal(calls.import.length, 1));
    assert.deepEqual(calls.import[0], { campaign_id: "camp-1", object_type: "Lead", limit: 2000, where: undefined, list_name: undefined });
    await waitFor(() => assert.ok(screen.getByTestId("salesforce-import-result")));
    assert.match(screen.getByTestId("salesforce-import-result").textContent ?? "", /imported 2/);
});
