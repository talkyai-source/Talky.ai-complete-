import React from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext, type AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import RegisterClientPage from "./register/register-client";
import AcceptInvitePage from "./accept-invite/page";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace } from "@/lib/http-client";
import { inviteApi } from "@/lib/team-api";

// QA F12-10. When the backend requires acceptance of the current Terms and
// Privacy Policy, sign-up and invite acceptance must send both versions.
// These tests run the real pages and the real API client against a fake network.

const email = "owner@example.test";
const TOKEN = "t".repeat(40);
const PASSWORD = "a long enough passphrase";
const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {}, bfcacheId: "test" } satisfies AppRouterInstance;
const anonymous = new ApiClientError({ status: 401, code: "unauthorized", message: "No browser session.", url: "/auth/me", method: "GET" });
const VERSIONS = { required: true, terms_version: "terms-v1", privacy_version: "privacy-v1" };

let legalAnswer: () => Response;
let submitAnswer: () => Response;
let sent: Array<{ path: string; body: Record<string, unknown> }>;
const refusal = (terms: string, privacy: string) => () => Response.json(
    { detail: { code: "legal_acceptance_required", message: "Accept the current Terms and Privacy Policy to continue", terms_version: terms, privacy_version: privacy } },
    { status: 422 },
);
const stop = () => Response.json({ detail: "stop here" }, { status: 400 });

beforeEach(() => {
    sent = [];
    legalAnswer = () => Response.json({ required: false, terms_version: "terms-v1", privacy_version: "privacy-v1" });
    submitAnswer = stop;
    localStorage.clear();
    clearFreshLoginGrace();
    mock.method(api, "getMe", async () => { throw anonymous; });
    mock.method(api, "signupStart", async () => ({ message: "Code sent", expires_in_minutes: 15, email }));
    mock.method(api, "signupVerifyCode", async () => ({ message: "ok", email }));
    mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/auth/legal-versions")) return legalAnswer();
        if (url.endsWith("/auth/signup/complete") || url.endsWith("/auth/invites/accept")) {
            sent.push({ path: url.slice(url.indexOf("/auth/")), body: JSON.parse(String(init?.body)) });
            return submitAnswer();
        }
        throw new Error(`Unexpected request to ${url}`);
    });
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.history.replaceState(null, "", "/");
    localStorage.clear();
    clearFreshLoginGrace();
});

const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;
const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });

async function reachPasswordStep() {
    render(<AppRouterContext.Provider value={router}>
        <SearchParamsContext.Provider value={new URLSearchParams("")}>
            <AuthProvider><RegisterClientPage /></AuthProvider>
        </SearchParamsContext.Provider>
    </AppRouterContext.Provider>);
    await tick();
    fireEvent.change(screen.getByLabelText("Your Name"), { target: { value: "Example Owner" } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: "Example" } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: email } });
    fireEvent.submit(screen.getByLabelText("Your Name").closest("form")!);
    const otp = await screen.findByLabelText("Verification code", {}, { timeout: 3000 });
    fireEvent.change(otp, { target: { value: "123456" } });
    fireEvent.submit(otp.closest("form")!);
    await screen.findByLabelText("Password", {}, { timeout: 3000 });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: PASSWORD } });
}

const createAccount = async () => { await act(async () => { fireEvent.submit(screen.getByLabelText("Password").closest("form")!); }); };
const checkbox = () => screen.queryByRole("checkbox", { name: /I agree to the Terms of Service and Privacy Policy/ }) as HTMLInputElement | null;

// ─── Sign-up ────────────────────────────────────────────────────────────────

test("sign-up with acceptance off shows no checkbox and sends neither version", async () => {
    await reachPasswordStep();
    assert.equal(checkbox(), null);
    assert.equal(button(/Create account/).disabled, false);
    await createAccount();
    await waitFor(() => assert.equal(sent.length, 1));
    assert.deepEqual(sent[0].body, { email, code: "123456", password: PASSWORD, confirm_password: PASSWORD });
});

test("sign-up with acceptance required cannot be submitted unticked, and sends both versions once ticked", async () => {
    legalAnswer = () => Response.json(VERSIONS);
    await reachPasswordStep();
    const box = await waitFor(() => { const found = checkbox(); assert.ok(found); return found; });
    assert.equal(box.checked, false);
    const labelled = screen.getByLabelText(/I agree to the/);
    assert.equal(labelled, box);
    const row = within(box.parentElement!);
    assert.equal(row.getByRole("link", { name: "Terms of Service" }).getAttribute("href"), "/terms");
    assert.equal(row.getByRole("link", { name: "Terms of Service" }).getAttribute("target"), "_blank");
    assert.equal(row.getByRole("link", { name: "Privacy Policy" }).getAttribute("href"), "/privacy");
    assert.equal(button(/Create account/).disabled, true);
    await createAccount();
    assert.equal(sent.length, 0, "nothing is sent while the box is unticked");
    fireEvent.click(box);
    assert.equal(button(/Create account/).disabled, false);
    await createAccount();
    await waitFor(() => assert.equal(sent.length, 1));
    assert.deepEqual(sent[0].body, {
        email, code: "123456", password: PASSWORD, confirm_password: PASSWORD,
        terms_version: "terms-v1", privacy_version: "privacy-v1",
    });
});

test("sign-up whose first read failed recovers from a 422 with the versions the server names", async () => {
    legalAnswer = () => Response.json({ detail: "down" }, { status: 503 });
    submitAnswer = refusal("terms-v2", "privacy-v2");
    await reachPasswordStep();
    assert.equal(checkbox(), null);
    await createAccount();
    await waitFor(() => assert.ok(checkbox()));
    assert.equal(checkbox()!.checked, false);
    const notice = await screen.findByText("Our terms were updated. Please review and accept them to continue.");
    assert.ok(checkbox()!.getAttribute("aria-describedby")!.split(" ").includes(notice.id));
    assert.equal(button(/Create account/).disabled, true);
    submitAnswer = stop;
    fireEvent.click(checkbox()!);
    await createAccount();
    await waitFor(() => assert.equal(sent.length, 2));
    assert.equal(sent[1].body.terms_version, "terms-v2");
    assert.equal(sent[1].body.privacy_version, "privacy-v2");
});

test("sign-up says it is unavailable when acceptance is required but no versions are configured", async () => {
    legalAnswer = () => Response.json({ required: true, terms_version: null, privacy_version: null });
    render(<AppRouterContext.Provider value={router}>
        <SearchParamsContext.Provider value={new URLSearchParams("")}>
            <AuthProvider><RegisterClientPage /></AuthProvider>
        </SearchParamsContext.Provider>
    </AppRouterContext.Provider>);
    await screen.findByText(/Sign-up is temporarily unavailable/);
    assert.equal(checkbox(), null);
    assert.equal(button(/Get started/).disabled, true);
});

// ─── Accept invite ──────────────────────────────────────────────────────────

async function openInvite() {
    mock.method(inviteApi, "preview", async () => ({ email: "sam@example.com", workspace_name: "Acme Ltd", role: "agent", expires_at: "2026-11-18T09:00:00Z" }));
    window.history.replaceState(null, "", `/auth/accept-invite#token=${TOKEN}`);
    render(<AcceptInvitePage />);
    await screen.findByLabelText("Your name", undefined, { timeout: 15_000 });
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Sam Agent" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: PASSWORD } });
}
const join = async () => { await act(async () => { fireEvent.submit(screen.getByLabelText("Your name").closest("form")!); }); };

test("accept-invite with acceptance off keeps today's notice and sends neither version", async () => {
    await openInvite();
    await tick();
    assert.equal(checkbox(), null);
    assert.ok(screen.getByText(/By joining, you agree to our/));
    await join();
    await waitFor(() => assert.equal(sent.length, 1));
    assert.deepEqual(sent[0].body, { token: TOKEN, name: "Sam Agent", password: PASSWORD, confirm_password: PASSWORD });
});

test("accept-invite with acceptance required cannot join unticked, and sends both versions once ticked", async () => {
    legalAnswer = () => Response.json(VERSIONS);
    await openInvite();
    const box = await waitFor(() => { const found = checkbox(); assert.ok(found); return found; });
    assert.equal(box.checked, false);
    assert.equal(button("Join the workspace").disabled, true);
    await join();
    assert.equal(sent.length, 0);
    fireEvent.click(box);
    await join();
    await waitFor(() => assert.equal(sent.length, 1));
    assert.deepEqual(sent[0].body, {
        token: TOKEN, name: "Sam Agent", password: PASSWORD, confirm_password: PASSWORD,
        terms_version: "terms-v1", privacy_version: "privacy-v1",
    });
});

test("accept-invite recovers from a 422 and lets the person retry with the new versions", async () => {
    legalAnswer = () => Response.json({ detail: "down" }, { status: 503 });
    submitAnswer = refusal("terms-v2", "privacy-v2");
    await openInvite();
    await tick();
    assert.equal(checkbox(), null);
    await join();
    await waitFor(() => assert.ok(checkbox()));
    await screen.findByText("Our terms were updated. Please review and accept them to continue.");
    assert.equal(checkbox()!.checked, false);
    submitAnswer = stop;
    fireEvent.click(checkbox()!);
    await join();
    await waitFor(() => assert.equal(sent.length, 2));
    assert.equal(sent[1].body.terms_version, "terms-v2");
    assert.equal(sent[1].body.privacy_version, "privacy-v2");
});
