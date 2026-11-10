import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiClientError } from "@/lib/http-client";
import { inviteApi, type InvitePreview } from "@/lib/team-api";
import AcceptInvitePage from "./page";

// QA F1-15 / D4-2: where an invited person joins. The token arrives in the
// link's fragment, is read once and removed from the address bar, and only
// travels in request bodies.
const TOKEN = "tok-abcdefghijklmnopqrstuvwxyz0123456789";
const SLOW = 15_000;
const INVITE: InvitePreview = { email: "sam@example.com", workspace_name: "Acme Ltd", role: "agent", expires_at: "2026-11-18T09:00:00Z" };

afterEach(() => { cleanup(); mock.restoreAll(); window.history.replaceState(null, "", "/"); });

function openLink(hash: string) {
    window.history.replaceState(null, "", `/auth/accept-invite${hash}`);
    render(<AcceptInvitePage />);
}

function refusal(status: number, message: string) {
    return new ApiClientError({ status, code: "http_error", message, url: "http://example.test/auth/invites", method: "POST" });
}

async function fillAndJoin(password = "a long enough passphrase", confirm = password) {
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "  Sam Agent " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: confirm } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Join the workspace" })); });
}

test("the invite is checked with the link's token, which then leaves the address bar", async () => {
    const preview = mock.method(inviteApi, "preview", async () => INVITE);
    openLink(`#token=${TOKEN}`);
    await screen.findByRole("heading", { level: 1, name: "Join Acme Ltd" }, { timeout: SLOW });
    assert.deepEqual(preview.mock.calls.at(-1)?.arguments, [TOKEN]);
    assert.equal(window.location.hash, "");
    assert.equal(window.location.href.includes(TOKEN), false);
    assert.equal(screen.getAllByText(/invited as Agent/).length, 1);
    assert.equal(screen.getAllByText(/sam@example\.com/).length, 1);
});

test("joining creates the account and points to sign-in", async () => {
    mock.method(inviteApi, "preview", async () => INVITE);
    const accept = mock.method(inviteApi, "accept", async () => ({ email: "sam@example.com", message: "joined" }));
    openLink(`#token=${TOKEN}`);
    await screen.findByLabelText("Your name", undefined, { timeout: SLOW });
    await fillAndJoin();
    await waitFor(() => assert.equal(accept.mock.callCount(), 1));
    assert.deepEqual(accept.mock.calls[0].arguments, [{ token: TOKEN, name: "Sam Agent", password: "a long enough passphrase", confirmPassword: "a long enough passphrase" }]);
    await screen.findByRole("heading", { level: 1, name: "You're in" });
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), "/auth/login");
});

test("a used or withdrawn link says so", async () => {
    mock.method(inviteApi, "preview", async () => { throw refusal(410, "gone"); });
    openLink(`#token=${TOKEN}`);
    await screen.findByRole("heading", { level: 1, name: "This invite cannot be used" }, { timeout: SLOW });
    assert.equal(screen.getAllByText(/no longer valid/).length, 1);
});

test("a link without its token says so and asks nothing of the server", async () => {
    const preview = mock.method(inviteApi, "preview", async () => INVITE);
    openLink("");
    await screen.findByRole("heading", { level: 1, name: "This link is incomplete" }, { timeout: SLOW });
    assert.equal(preview.mock.callCount(), 0);
});

test("passwords are checked before anything is sent", async () => {
    mock.method(inviteApi, "preview", async () => INVITE);
    const accept = mock.method(inviteApi, "accept", async () => ({ email: "sam@example.com", message: "joined" }));
    openLink(`#token=${TOKEN}`);
    await screen.findByLabelText("Your name", undefined, { timeout: SLOW });
    await fillAndJoin("a long enough passphrase", "a different passphrase!");
    assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === "Passwords do not match."), true);
    await fillAndJoin("short", "short");
    assert.equal(screen.queryAllByRole("alert").some((node) => /at least 15 characters/.test(node.textContent ?? "")), true);
    assert.equal(accept.mock.callCount(), 0);
});

test("the server's refusal is shown, and a link spent meanwhile turns the page", async () => {
    mock.method(inviteApi, "preview", async () => INVITE);
    const answers = [refusal(409, "This email already has an account. Sign in instead."), refusal(410, "gone")];
    mock.method(inviteApi, "accept", async () => { throw answers.shift(); });
    openLink(`#token=${TOKEN}`);
    await screen.findByLabelText("Your name", undefined, { timeout: SLOW });
    await fillAndJoin();
    await waitFor(() => assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === "This email already has an account. Sign in instead."), true));
    await fillAndJoin();
    await screen.findByRole("heading", { level: 1, name: "This invite cannot be used" });
});
