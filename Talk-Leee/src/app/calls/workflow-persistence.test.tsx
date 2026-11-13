import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { sharedHttpClient } from "@/lib/api";
import type { HttpRequestOptions } from "@/lib/http-client";
import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";

const tenantA = "11111111-1111-4111-8111-111111111111";
const tenantB = "22222222-2222-4222-8222-222222222222";
const actorA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const actorB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const call: Call = {
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    campaign_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    campaign_name: "Workflow regression",
    lead_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    phone_number: "+15550001111",
    status: "completed",
    created_at: "2026-11-06T10:00:00.000Z",
};
const callsData = { calls: [call], total: 1 };
let currentUser = { id: actorA, tenant_id: tenantA };
const permissions = { isSuccess: true, data: { permissions: ["calls:read", "calls:create"] } };

// Exercise the actual page and its actual notes/lead/form controls. Only the
// unrelated shell, redial/banner widgets and already-tested auth/list queries
// are replaced. The workflow HTTP client below remains the real shared client.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> };
    }
    if (id.endsWith("components/calls/dial-again-button")) return { DialAgainButton: () => null };
    if (id.endsWith("components/calls/call-issues-banner")) return { CallIssuesBanner: () => null };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user: currentUser, isLoading: false }) };
    if (id.endsWith("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissions };
    if (id.endsWith("lib/api-hooks")) {
        return {
            useCalls: () => ({ data: callsData, isLoading: false, isError: false, refetch: async () => undefined }),
            useCallSummary: () => ({ data: undefined, isLoading: false, isError: false }),
            useCallTranscript: () => ({ data: undefined, isLoading: false, isError: false }),
        };
    }
    return originalLoad.call(this, id, ...args);
};
let CallsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped unrelated dependency stubs above
    CallsPage = require("./page").default;
} finally {
    loader._load = originalLoad;
}

if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

type WorkflowBody = Omit<CallHistoryWorkflowEntry, "updatedAt"> & { create_only?: boolean };
type RequestRecord = HttpRequestOptions & { actor: string; tenant: string };
let requests: RequestRecord[] = [];
const path = `/calls/${call.id}/workflow`;
const legacyKey = `talklee.call-history.workflow.v1:${encodeURIComponent(tenantA)}`;

function entry(notes = "Colleague's saved notes"): CallHistoryWorkflowEntry {
    return {
        leadType: "warm",
        notes,
        form: { contact: "Colleague", interest: "Annual plan", nextStep: "Call Friday", completed: false },
        updatedAt: "2026-11-06T09:00:00.000Z",
    };
}

function body(value: CallHistoryWorkflowEntry): WorkflowBody {
    return { leadType: value.leadType, notes: value.notes, form: { ...value.form } };
}

function submitted(request: RequestRecord): WorkflowBody {
    assert.ok(request.body && typeof request.body === "object");
    return request.body as WorkflowBody;
}

function putRequests() {
    return requests.filter((request) => request.method === "PUT");
}

function getRequests() {
    return requests.filter((request) => !request.method || request.method === "GET");
}

function transport(handler: (request: RequestRecord) => unknown | Promise<unknown>) {
    mock.method(sharedHttpClient(), "request", async (options: HttpRequestOptions) => {
        assert.equal(options.path, path, "Only the owned call workflow endpoint may be requested");
        assert.ok(!options.method || ["GET", "PUT"].includes(options.method));
        const request = { ...options, body: options.body === undefined ? undefined : structuredClone(options.body), actor: currentUser.id, tenant: currentUser.tenant_id };
        requests.push(request);
        return handler(request);
    });
}

function accept(request: RequestRecord) {
    const value = submitted(request);
    return { workflow: { leadType: value.leadType, notes: value.notes, form: value.form, updatedAt: "2026-11-06T11:00:00.000Z" }, created: false };
}

function notesInputs() {
    return screen.getAllByRole("textbox", { name: `Notes for ${call.phone_number}` }) as HTMLInputElement[];
}

async function expectNotes(value: string) {
    await waitFor(() => {
        const inputs = notesInputs();
        assert.ok(inputs.length > 0);
        assert.ok(inputs.every((input) => input.value === value), `Expected server/draft notes: ${value}; got ${inputs.map((input) => input.value).join(" | ")}`);
    });
}

function editNotes(value: string) {
    fireEvent.change(notesInputs()[0], { target: { value } });
}

function retry() {
    fireEvent.click(screen.getAllByRole("button", { name: /retry/i })[0]);
}

async function expectUnsaved() {
    await waitFor(() => assert.ok(screen.getAllByRole("alert").some((alert) => /not saved|could not save|couldn't save|unable to save|could not load|couldn't load|not synced/i.test(alert.textContent ?? ""))));
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
}

beforeEach(() => {
    currentUser = { id: actorA, tenant_id: tenantA };
    requests = [];
    localStorage.clear();
    window.history.replaceState({}, "", "/calls");
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request in workflow persistence test"); });
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    localStorage.clear();
});

test("call history loads saved server notes for both colleagues without writing browser defaults", async () => {
    const saved = entry();
    transport(() => ({ workflow: saved }));
    const first = render(<CallsPage />);
    await expectNotes(saved.notes);
    assert.equal(getRequests().length, 1);
    assert.equal(putRequests().length, 0);
    first.unmount();
    currentUser = { id: actorB, tenant_id: tenantA };
    render(<CallsPage />);
    await waitFor(() => assert.equal(getRequests().length, 2));
    await expectNotes(saved.notes);
    assert.equal(putRequests().length, 0);
});

test("editing notes sends the exact durable workflow and preserves loaded lead/form fields", async () => {
    const saved = entry();
    transport((request) => request.method === "PUT" ? accept(request) : { workflow: saved });
    render(<CallsPage />);
    await expectNotes(saved.notes);
    editNotes("Shared follow-up notes");
    await waitFor(() => assert.equal(putRequests().length, 1));
    assert.deepEqual(submitted(putRequests()[0]), { ...body(saved), notes: "Shared follow-up notes" });
    await expectNotes("Shared follow-up notes");
});

test("the real lead selector and completed post-call form both persist through the workflow API", async () => {
    const saved = entry();
    transport((request) => request.method === "PUT" ? accept(request) : { workflow: saved });
    render(<CallsPage />);
    await expectNotes(saved.notes);
    const user = userEvent.setup({ document });
    await user.click(screen.getAllByRole("combobox", { name: /lead type for/i })[0]);
    await user.click(screen.getByRole("option", { name: "Hot" }));
    await waitFor(() => assert.equal(putRequests().length, 1));
    assert.deepEqual(submitted(putRequests()[0]), { ...body(saved), leadType: "hot" });
    await user.click(screen.getAllByRole("button", { name: /open form for/i })[0]);
    fireEvent.change(screen.getByLabelText(/contact or decision maker/i), { target: { value: "Ava" } });
    fireEvent.change(screen.getByLabelText(/key need or interest/i), { target: { value: "Updated need" } });
    fireEvent.change(screen.getByLabelText(/next step/i), { target: { value: "Send proposal" } });
    await user.click(screen.getByRole("button", { name: /^complete form$/i }));
    await waitFor(() => assert.equal(putRequests().length, 2));
    assert.deepEqual(submitted(putRequests()[1]), {
        ...body(saved), leadType: "hot",
        form: { contact: "Ava", interest: "Updated need", nextStep: "Send proposal", completed: true },
    });
});

for (const alreadySaved of [false, true]) {
    test(`legacy browser migration uses create_only, preserves backup and confirms once (server row ${alreadySaved ? "exists" : "missing"})`, async () => {
        const legacy = entry("Original device backup");
        const original = JSON.stringify({ [call.id]: legacy });
        localStorage.setItem(legacyKey, original);
        let server = alreadySaved ? entry("Canonical server notes") : null;
        transport((request) => {
            if (request.method !== "PUT") return { workflow: server };
            assert.deepEqual(submitted(request), { ...body(legacy), create_only: true });
            if (!server) server = { ...legacy, updatedAt: "2026-11-06T11:00:00.000Z" };
            return { workflow: server, created: !alreadySaved };
        });
        const first = render(<CallsPage />);
        await waitFor(() => assert.equal(putRequests().length, 1));
        await expectNotes(server!.notes);
        assert.equal(localStorage.getItem(legacyKey), original);
        first.unmount();
        render(<CallsPage />);
        await waitFor(() => assert.equal(getRequests().length, 2));
        await expectNotes(server!.notes);
        assert.equal(putRequests().length, 1, "Confirmed migration must not be imported again on reopening");
        assert.equal(localStorage.getItem(legacyKey), original);
    });
}

test("a failed legacy import remains visibly unfinished and keeps its backup until explicit retry succeeds", async () => {
    const legacy = entry("Unmigrated notes");
    const original = JSON.stringify({ [call.id]: legacy });
    localStorage.setItem(legacyKey, original);
    let fail = true;
    transport((request) => {
        if (request.method !== "PUT") return { workflow: null };
        assert.deepEqual(submitted(request), { ...body(legacy), create_only: true });
        if (fail) throw new Error("Temporary workflow outage");
        return { workflow: legacy, created: true };
    });
    render(<CallsPage />);
    await waitFor(() => assert.equal(putRequests().length, 1));
    await expectUnsaved();
    assert.equal(localStorage.getItem(legacyKey), original);
    fail = false;
    retry();
    await waitFor(() => assert.equal(putRequests().length, 2));
    await expectNotes(legacy.notes);
    await waitFor(() => assert.equal(screen.queryAllByRole("alert").length, 0));
    assert.equal(localStorage.getItem(legacyKey), original);
});

test("a rejected edit survives reopening and explicit retry merges the pending patch with fresh server fields", async () => {
    let saved = entry();
    let fail = true;
    transport((request) => {
        if (request.method !== "PUT") return { workflow: saved };
        if (fail) throw new Error("Temporary workflow outage");
        return accept(request);
    });
    const first = render(<CallsPage />);
    await expectNotes(saved.notes);
    editNotes("Keep my unsaved note");
    await waitFor(() => assert.equal(putRequests().length, 1));
    await expectUnsaved();
    first.unmount();
    saved = { ...saved, form: { ...saved.form, contact: "Another colleague's newer contact" } };
    render(<CallsPage />);
    await waitFor(() => assert.equal(getRequests().length, 2));
    await expectNotes("Keep my unsaved note");
    await expectUnsaved();
    assert.equal(putRequests().length, 1, "Reopening must not silently overwrite the server with an old failed draft");
    fail = false;
    retry();
    await waitFor(() => assert.equal(putRequests().length, 2));
    assert.deepEqual(submitted(putRequests()[1]), { ...body(saved), notes: "Keep my unsaved note" });
    await waitFor(() => assert.equal(screen.queryAllByRole("alert").length, 0));
});

test("an older in-flight save cannot replace a newer edit and saves for one call are serialized", async () => {
    const saved = entry();
    const held = deferred<ReturnType<typeof accept>>();
    let firstFinished = false;
    let overlappingPut = false;
    transport((request) => {
        if (request.method !== "PUT") return { workflow: saved };
        if (putRequests().length === 1) return held.promise.finally(() => { firstFinished = true; });
        if (!firstFinished) overlappingPut = true;
        return accept(request);
    });
    render(<CallsPage />);
    await expectNotes(saved.notes);
    editNotes("First edit");
    await waitFor(() => assert.equal(putRequests().length, 1));
    editNotes("Newer edit");
    await expectNotes("Newer edit");
    await act(async () => { held.resolve(accept(putRequests()[0])); });
    await waitFor(() => assert.equal(putRequests().length, 2));
    assert.equal(overlappingPut, false, "The second PUT must not overtake the first PUT");
    assert.equal(submitted(putRequests()[1]).notes, "Newer edit");
    await expectNotes("Newer edit");
});

test("an edit made during initial loading waits and preserves the server's unrelated fields", async () => {
    const saved = entry();
    const held = deferred<{ workflow: CallHistoryWorkflowEntry }>();
    transport((request) => request.method === "PUT" ? accept(request) : held.promise);
    render(<CallsPage />);
    await waitFor(() => assert.equal(getRequests().length, 1));
    editNotes("Typed before loading finished");
    assert.equal(putRequests().length, 0);
    await act(async () => { held.resolve({ workflow: saved }); });
    await waitFor(() => assert.equal(putRequests().length, 1));
    assert.deepEqual(submitted(putRequests()[0]), { ...body(saved), notes: "Typed before loading finished" });
    await expectNotes("Typed before loading finished");
});

test("a failed initial load cannot save guessed empty fields; retry loads before applying the pending edit", async () => {
    const saved = entry();
    let fail = true;
    transport((request) => {
        if (request.method === "PUT") return accept(request);
        if (fail) throw new Error("Workflow read unavailable");
        return { workflow: saved };
    });
    render(<CallsPage />);
    await waitFor(() => assert.equal(getRequests().length, 1));
    await expectUnsaved();
    editNotes("Preserve during failed load");
    assert.equal(putRequests().length, 0);
    fail = false;
    retry();
    await waitFor(() => assert.equal(putRequests().length, 1));
    assert.ok(getRequests().length >= 2);
    assert.deepEqual(submitted(putRequests()[0]), { ...body(saved), notes: "Preserve during failed load" });
});

for (const switchTenant of [false, true]) {
    test(`late workflow responses are discarded after ${switchTenant ? "tenant" : "actor"} changes`, async () => {
        const held = deferred<{ workflow: CallHistoryWorkflowEntry }>();
        transport((request) => request.actor === actorA ? held.promise : { workflow: entry("New session notes") });
        const page = render(<CallsPage />);
        await waitFor(() => assert.equal(getRequests().length, 1));
        const oldRequest = getRequests()[0];
        currentUser = { id: actorB, tenant_id: switchTenant ? tenantB : tenantA };
        page.rerender(<CallsPage />);
        await expectNotes("New session notes");
        assert.ok(oldRequest.signal?.aborted || oldRequest.ownsOperation?.() === false, "Old-session requests must stop owning HTTP retries");
        await act(async () => { held.resolve({ workflow: entry("Old session confidential notes") }); });
        await expectNotes("New session notes");
        assert.equal(putRequests().length, 0);
    });
}
