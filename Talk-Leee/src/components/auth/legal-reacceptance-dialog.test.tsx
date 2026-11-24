import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { sharedHttpClient } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";

// The real dialog, Modal, Link and LogoutButton run. Only the auth context and the router are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let pathname = "/dashboard";
let user: Record<string, unknown> | null = null;
const refreshUser = mock.fn(async (_opts?: unknown) => undefined);
const logout = mock.fn(async () => ({ serverConfirmed: true, identityCurrent: true }));
const pushed: string[] = [];
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => pathname, useRouter: () => ({ push: (to: string) => { pushed.push(to); }, replace() {} }) };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user, refreshUser, logout }) };
    return originalLoad.call(this, id, ...args);
};
let Dialog: React.ComponentType<{ onAccepted?: () => void }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Dialog = require("./legal-reacceptance-dialog").LegalReacceptanceDialog;
} finally {
    loader._load = originalLoad;
}

const owed = { id: "u1", email: "u@example.test", role: "tenant_admin", tenant_id: "t1", legal_acceptance_required: true, terms_version: "2026-11-01", privacy_version: "2026-11-02" };
const BUTTON = /Accept and continue/;
const BOX = /I have read and accept the Terms of Service and the Privacy Policy/;

let accepted = 0;
function show() {
    return render(<QueryClientProvider client={new QueryClient()}><Dialog onAccepted={() => { accepted++; }} /></QueryClientProvider>);
}
let requests: Array<Record<string, unknown>>;
let outcome: () => Promise<unknown>;
beforeEach(() => {
    accepted = 0; pathname = "/dashboard"; user = { ...owed }; requests = []; pushed.length = 0;
    refreshUser.mock.resetCalls(); logout.mock.resetCalls();
    outcome = async () => ({ accepted: true });
    mock.method(sharedHttpClient(), "request", async (opts: Record<string, unknown>) => { requests.push(opts); return outcome(); });
});
afterEach(() => { cleanup(); mock.restoreAll(); });

test("it appears when /auth/me says an acceptance is owed, with the words, both links and a disabled button", () => {
    show();
    assert.ok(screen.getByRole("dialog"));
    assert.ok(screen.getByText("Our terms have been updated"));
    assert.equal(screen.getByRole("link", { name: "Terms of Service" }).getAttribute("href"), "/terms");
    assert.equal(screen.getByRole("link", { name: "Terms of Service" }).getAttribute("target"), "_blank");
    assert.equal(screen.getByRole("link", { name: "Privacy Policy" }).getAttribute("href"), "/privacy");
    assert.equal((screen.getByRole("button", { name: BUTTON }) as HTMLButtonElement).disabled, true);
    fireEvent.click(screen.getByLabelText(BOX));
    assert.equal((screen.getByRole("button", { name: BUTTON }) as HTMLButtonElement).disabled, false);
});

test("it offers a Contact us link to the public contact page, opening in a new tab", () => {
    show();
    const link = screen.getByRole("link", { name: "Contact us" });
    assert.equal(link.getAttribute("href"), "/contact");
    assert.equal(link.getAttribute("target"), "_blank");
    assert.equal(link.getAttribute("rel"), "noopener noreferrer");
    assert.ok(screen.getByText(/Questions\?/));
});

test("it does not appear when the field is false, missing, or the user is not known yet", () => {
    for (const next of [{ ...owed, legal_acceptance_required: false }, { id: "u1", email: "u@example.test", role: "tenant_admin" }, null,
        { ...owed, legal_acceptance_required: "true" }, { ...owed, terms_version: null }]) {
        user = next as typeof user;
        const view = show();
        assert.equal(view.container.ownerDocument.querySelector('[role="dialog"]'), null, JSON.stringify(next));
        view.unmount();
    }
});

test("it does not appear on a public page or an auth page", () => {
    for (const path of ["/terms", "/pricing", "/auth/login", "/auth/accept-invite"]) {
        pathname = path;
        const view = show();
        assert.equal(view.container.ownerDocument.querySelector('[role="dialog"]'), null, path);
        view.unmount();
    }
});

test("accepting sends the server's versions, re-reads /auth/me and closes", async () => {
    show();
    fireEvent.click(screen.getByLabelText(BOX));
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    await waitFor(() => assert.equal(screen.queryAllByRole("dialog").length === 0, true));
    assert.deepEqual(requests.map(r => [r.path, r.method, r.body]),
        [["/auth/legal-acceptance", "POST", { terms_version: "2026-11-01", privacy_version: "2026-11-02" }]]);
    assert.equal(refreshUser.mock.callCount(), 1);
    assert.equal(accepted, 1, "the page is loaded again so every refused read recovers");
});

test("a failed save keeps the dialog open and says so in plain words", async () => {
    outcome = async () => { throw new ApiClientError({ status: 500, code: "server_error", message: "boom", url: "/auth/legal-acceptance", method: "POST" }); };
    show();
    fireEvent.click(screen.getByLabelText(BOX));
    fireEvent.click(screen.getByRole("button", { name: BUTTON }));
    const alert = await screen.findByRole("alert");
    assert.match(alert.textContent ?? "", /We could not save your acceptance\. Please try again\./);
    assert.ok(screen.getByRole("dialog"));
    assert.equal(refreshUser.mock.callCount(), 0);
    assert.equal(accepted, 0, "a failed save does not reload the page");
    assert.equal((screen.getByRole("button", { name: BUTTON }) as HTMLButtonElement).disabled, false);
});

test("Escape and the backdrop do not close it", () => {
    show();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(document.querySelector('[aria-hidden="true"].absolute') as Element);
    assert.ok(screen.getByRole("dialog"));
});

test("Sign out from the dialog uses the existing sign-out", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: /Sign out/ }));
    await waitFor(() => assert.equal(logout.mock.callCount(), 1));
    await waitFor(() => assert.deepEqual(pushed, ["/auth/login"]));
});
