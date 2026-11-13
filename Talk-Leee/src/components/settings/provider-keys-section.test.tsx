import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { ProviderKeysSection } from "./provider-keys-section";

const originalRequest = api.request;
const tenantA = "11111111-1111-4111-8111-111111111111";
const tenantB = "22222222-2222-4222-8222-222222222222";
const actorA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const keyId = "33333333-3333-4333-8333-333333333333";
const secret = "synthetic-private-byo-key-1234";
const checkedAt = "2026-11-06T01:00:00+00:00";
const path = "/tenant-ai-credentials/";
type Request = Parameters<typeof api.request>[0];
type SafeCredential = {
    id: string; tenant_id: string; provider: string; credential_kind: string;
    last4: string; label: string | null; status: "active" | "disabled";
    validation_status: "valid" | "invalid" | null; last_checked_at: string | null;
};

function row(overrides: Partial<SafeCredential> = {}): SafeCredential {
    return { id: keyId, tenant_id: tenantA, provider: "groq", credential_kind: "api_key",
        last4: "0000", label: "Existing key", status: "active", validation_status: null,
        last_checked_at: null, ...overrides };
}

afterEach(() => { cleanup(); api.request = originalRequest; });

function transport(handler: (request: Request) => Promise<unknown> | unknown) {
    const requests: Request[] = [];
    api.request = async <T,>(request: Request) => {
        assert.ok(request.path.startsWith(path), "Only the current credential API may be called");
        requests.push(request);
        return await handler(request) as T;
    };
    return requests;
}

function mount(canEdit = true) {
    return render(<ProviderKeysSection tenantId={tenantA} userId={actorA} canEdit={canEdit} />);
}

function inputKey() {
    const input = screen.getByLabelText("API key") as HTMLInputElement;
    assert.equal(input.type, "password");
    fireEvent.change(input, { target: { value: secret } });
    return input;
}

test("saved keys show masked suffix, independent validity and last-checked time", async () => {
    transport(() => [row({ validation_status: "valid", last_checked_at: checkedAt })]);
    mount();
    await screen.findByText("Existing key");
    assert.ok(screen.getByText("Valid"));
    assert.ok(screen.getByText(/0000/));
    assert.equal(document.querySelector("time")?.getAttribute("datetime"), checkedAt);
    assert.match(document.body.textContent ?? "", /does not verify call readiness/i);
    assert.equal(document.body.textContent?.includes(secret), false);
});

test("legacy keys are not presented as valid merely because they are active", async () => {
    transport(() => [row()]);
    mount();
    await screen.findByText("Existing key");
    assert.ok(screen.getByText("Not checked"));
    assert.equal(screen.queryByText("Valid"), null);
});

test("save validates through the existing endpoint and clears the password input", async () => {
    const requests = transport(request => request.method === "POST"
        ? row({ id: "44444444-4444-4444-8444-444444444444", label: "New key", last4: "1234", validation_status: "valid", last_checked_at: checkedAt })
        : []);
    mount();
    await screen.findByRole("heading", { name: "Provider API keys" });
    const input = inputKey();
    fireEvent.change(screen.getByLabelText("Label"), { target: { value: "New key" } });
    fireEvent.click(screen.getByRole("button", { name: "Validate and save" }));
    await screen.findByText("New key");
    const writes = requests.filter(request => request.method === "POST");
    assert.equal(writes.length, 1);
    assert.equal(writes[0].path, path);
    assert.deepEqual(writes[0].body, { provider: "groq", api_key: secret, credential_kind: "api_key", label: "New key" });
    assert.equal(input.value, "");
    for (let i = 0; i < localStorage.length; i++) assert.equal(localStorage.getItem(localStorage.key(i)!)?.includes(secret), false);
    for (let i = 0; i < sessionStorage.length; i++) assert.equal(sessionStorage.getItem(sessionStorage.key(i)!)?.includes(secret), false);
});

test("failed validation retains the previously saved key and reports the failure", async () => {
    transport(request => {
        if (request.method === "POST") throw new Error("Provider rejected this key. Existing key unchanged.");
        return [row({ validation_status: "valid", last_checked_at: checkedAt })];
    });
    mount();
    await screen.findByText("Existing key");
    inputKey();
    fireEvent.click(screen.getByRole("button", { name: "Validate and save" }));
    await screen.findByText("Provider rejected this key. Existing key unchanged.");
    assert.ok(screen.getByText("Existing key"));
    assert.ok(screen.getByText("Valid"));
});

test("recheck displays invalid status without claiming the key was disabled", async () => {
    const requests = transport(request => request.method === "POST"
        ? row({ validation_status: "invalid", last_checked_at: checkedAt }) : [row()]);
    mount();
    await screen.findByText("Existing key");
    fireEvent.click(screen.getByRole("button", { name: "Check key" }));
    await screen.findByText("Invalid");
    assert.ok(screen.getByText("Active"));
    assert.equal(requests.filter(request => request.method === "POST")[0].path, path + keyId + "/check");
});

test("disable uses tenant credential endpoint and preserves the history row", async () => {
    const requests = transport(request => request.method === "DELETE" ? undefined : [row()]);
    mount();
    await screen.findByText("Existing key");
    fireEvent.click(screen.getByRole("button", { name: "Disable key" }));
    await screen.findByText("Disabled");
    assert.ok(screen.getByText("Existing key"));
    assert.equal(requests.filter(request => request.method === "DELETE")[0].path, path + keyId);
});

test("ordinary members see status but cannot submit, recheck or disable", async () => {
    const requests = transport(() => [row()]);
    mount(false);
    await screen.findByText("Existing key");
    assert.equal(screen.queryByLabelText("API key"), null);
    assert.equal(screen.queryByRole("button", { name: "Check key" }), null);
    assert.equal(screen.queryByRole("button", { name: "Disable key" }), null);
    assert.equal(requests.some(request => request.method && request.method !== "GET"), false);
});

test("one pending save cannot be submitted twice", async () => {
    let finish!: (value: SafeCredential) => void;
    const requests = transport(request => request.method === "POST"
        ? new Promise<SafeCredential>(resolve => { finish = resolve; }) : []);
    mount();
    await screen.findByRole("heading", { name: "Provider API keys" });
    inputKey();
    const button = screen.getByRole("button", { name: "Validate and save" });
    fireEvent.click(button);
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, true));
    fireEvent.click(button);
    assert.equal(requests.filter(request => request.method === "POST").length, 1);
    await act(async () => finish(row({ validation_status: "valid", last_checked_at: checkedAt })));
});

test("tenant or actor change clears the draft and ignores old list responses", async () => {
    let finishOld!: (value: SafeCredential[]) => void;
    let count = 0;
    transport(() => ++count === 1 ? new Promise<SafeCredential[]>(resolve => { finishOld = resolve; }) : []);
    const mounted = mount();
    inputKey();
    mounted.rerender(<ProviderKeysSection tenantId={tenantB} userId="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" canEdit />);
    await waitFor(() => assert.equal((screen.getByLabelText("API key") as HTMLInputElement).value, ""));
    await act(async () => finishOld([row()]));
    assert.equal(screen.queryByText("Existing key"), null);
});

test("changing provider sends the selected known provider without changing global AI selection", async () => {
    const requests = transport(request => request.method === "POST"
        ? row({ provider: "openai", validation_status: "valid", last_checked_at: checkedAt }) : []);
    mount();
    await screen.findByRole("heading", { name: "Provider API keys" });
    fireEvent.click(screen.getByRole("combobox", { name: "Provider" }));
    fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: "OpenAI" }));
    inputKey();
    fireEvent.click(screen.getByRole("button", { name: "Validate and save" }));
    await waitFor(() => assert.equal(requests.filter(request => request.method === "POST").length, 1));
    assert.equal((requests.find(request => request.method === "POST")!.body as { provider: string }).provider, "openai");
    assert.equal(requests.some(request => request.path.includes("ai-options")), false);
});
