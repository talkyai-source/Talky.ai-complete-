import React from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext, type AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import LoginClientPage from "./login/login-client";
import RegisterClientPage from "./register/register-client";
import ForgotPasswordPage from "./forgot-password/page";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace } from "@/lib/http-client";

// QA F4-12. The backend verifies a Cloudflare Turnstile token on sign-in,
// sign-up start and forgot-password, and reads it from the JSON body as
// `turnstile_token`. These tests run the real pages and the real API client
// against a fake network and a fake Cloudflare script, and read what is sent.

// Cloudflare's published always-pass test site key (not a secret).
const SITE_KEY = "1x00000000000000000000AA";
const SCRIPT = 'script[src*="challenges.cloudflare.com"]';
const email = "owner@example.test";
const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {}, bfcacheId: "test" } satisfies AppRouterInstance;
const anonymous = new ApiClientError({ status: 401, code: "unauthorized", message: "No browser session.", url: "/auth/me", method: "GET" });
const oldKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;

type Challenge = { container: HTMLElement; options: { sitekey: string; action: string; callback: (token: string) => void } };
let challenges: Challenge[];
let sent: Array<{ path: string; body: Record<string, unknown> }>;
const answers: Record<string, () => Response> = {
    "/auth/login": () => Response.json({ detail: "Invalid email or password" }, { status: 401 }),
    "/auth/signup/start": () => Response.json({ message: "Code sent", expires_in_minutes: 15, email }),
    "/auth/forgot-password": () => Response.json({ message: "Reset code requested." }),
};
const cloudflare = window as unknown as { turnstile?: unknown };

// Stands in for Cloudflare's api.js: records every widget the page draws.
function installCloudflare() {
    cloudflare.turnstile = {
        render(container: HTMLElement, options: Challenge["options"]) {
            challenges.push({ container, options });
            return `widget-${challenges.length}`;
        },
        remove() {},
    };
}

beforeEach(() => {
    challenges = [];
    sent = [];
    localStorage.clear();
    clearFreshLoginGrace();
    mock.method(api, "getMe", async () => { throw anonymous; });
    mock.method(globalThis, "fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const path = Object.keys(answers).find((candidate) => url.endsWith(candidate));
        if (!path) throw new Error(`Unexpected request to ${url}`);
        sent.push({ path, body: JSON.parse(String(init?.body)) });
        return answers[path]();
    });
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    document.querySelectorAll(SCRIPT).forEach((script) => script.remove());
    delete cloudflare.turnstile;
    localStorage.clear();
    clearFreshLoginGrace();
    if (oldKey === undefined) delete process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY;
    else process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = oldKey;
});

function draw(pageUnderTest: React.ReactNode, query = "") {
    return render(<AppRouterContext.Provider value={router}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>
            <AuthProvider>{pageUnderTest}</AuthProvider>
        </SearchParamsContext.Provider>
    </AppRouterContext.Provider>);
}

/** Completes the challenge the page is showing right now, if it is showing one. */
async function solveChallenge(token: string) {
    await act(async () => { await Promise.resolve(); });
    const shown = challenges.filter((challenge) => challenge.container.isConnected).at(-1);
    if (shown) await act(async () => { shown.options.callback(token); });
}
const bodies = (path: string) => sent.filter((request) => request.path === path).map((request) => request.body);
// The backend also compares the action Cloudflare echoes back with the form's own.
const drawnFor = (action: string) => {
    assert.ok(challenges.length > 0, "a widget was drawn");
    for (const challenge of challenges) {
        assert.equal(challenge.options.action, action);
        assert.equal(challenge.options.sitekey, SITE_KEY);
    }
};
const button = (name: string | RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;
const withKey = () => { process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY = SITE_KEY; installCloudflare(); };
const withoutKey = () => { delete process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY; installCloudflare(); };

// ─── Sign-in ────────────────────────────────────────────────────────────────

async function reachPasswordStep() {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
    await solveChallenge("token-1"); // wherever the page asks for it before the password step
    fireEvent.click(button("Continue with password"));
    return screen.findByLabelText("Password", {}, { timeout: 3000 });
}

test("sign-in sends the token the widget produced, and a spent token is never reused", async () => {
    withKey();
    draw(<LoginClientPage />);
    const password = await reachPasswordStep();
    fireEvent.change(password, { target: { value: "wrong-password" } });
    await solveChallenge("token-1");
    const form = password.closest("form")!;
    fireEvent.submit(form);
    assert.equal((await screen.findByRole("alert")).textContent, "Invalid email or password");
    assert.deepEqual(bodies("/auth/login"), [{ email, password: "wrong-password", turnstile_token: "token-1" }]);

    // Cloudflare accepts a token once, so the retry needs a new challenge.
    assert.equal(button("Sign in").disabled, true);
    await solveChallenge("token-2");
    assert.equal(button("Sign in").disabled, false);
    fireEvent.submit(form);
    await waitFor(() => assert.equal(bodies("/auth/login").length, 2));
    assert.equal(bodies("/auth/login")[1].turnstile_token, "token-2");
    drawnFor("login");
});

test("sign-in without a site key is unchanged: no widget, no script, no token in the request", async () => {
    withoutKey();
    draw(<LoginClientPage />);
    const password = await reachPasswordStep();
    fireEvent.change(password, { target: { value: "wrong-password" } });
    assert.equal(button("Sign in").disabled, false);
    fireEvent.submit(password.closest("form")!);
    assert.equal((await screen.findByRole("alert")).textContent, "Invalid email or password");
    assert.deepEqual(bodies("/auth/login"), [{ email, password: "wrong-password" }]);
    assert.deepEqual(challenges, []);
    assert.equal(document.querySelectorAll(SCRIPT).length, 0);
});

// ─── Sign-up ────────────────────────────────────────────────────────────────

const signup = { name: "Example Owner", business_name: "Example", email };
function fillSignupForm() {
    fireEvent.change(screen.getByLabelText("Your Name"), { target: { value: signup.name } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: signup.business_name } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: email } });
}

test("sign-up sends the token the widget produced, and resending the code needs a new one", async () => {
    withKey();
    draw(<RegisterClientPage />);
    fillSignupForm();
    await solveChallenge("token-1");
    fireEvent.click(button("Get started"));
    await screen.findByLabelText("Verification code");
    assert.deepEqual(bodies("/auth/signup/start"), [{ ...signup, turnstile_token: "token-1" }]);

    assert.equal(button(/Resend/).disabled, true);
    await solveChallenge("token-2");
    assert.equal(button(/Resend/).disabled, false);
    fireEvent.click(button(/Resend/));
    await waitFor(() => assert.equal(bodies("/auth/signup/start").length, 2));
    assert.deepEqual(bodies("/auth/signup/start")[1], { ...signup, turnstile_token: "token-2" });
    drawnFor("signup");
});

test("sign-up waits for the challenge before it can be submitted", () => {
    withKey();
    draw(<RegisterClientPage />);
    assert.equal(button("Get started").disabled, true);
});

test("sign-up without a site key is unchanged: no widget, no script, no token in the request", async () => {
    withoutKey();
    draw(<RegisterClientPage />);
    fillSignupForm();
    assert.equal(button("Get started").disabled, false);
    fireEvent.click(button("Get started"));
    await screen.findByLabelText("Verification code");
    assert.deepEqual(bodies("/auth/signup/start"), [signup]);
    assert.equal(button(/Resend/).disabled, false);
    assert.deepEqual(challenges, []);
    assert.equal(document.querySelectorAll(SCRIPT).length, 0);
});

// ─── Forgot password ────────────────────────────────────────────────────────

const forgotQuery = `email=${encodeURIComponent(email)}`;

test("forgot-password sends the token the widget produced, and a new code needs a new one", async () => {
    withKey();
    draw(<ForgotPasswordPage />, forgotQuery);
    await solveChallenge("token-1");
    fireEvent.click(button("Send reset code"));
    await screen.findByLabelText("Code");
    assert.deepEqual(bodies("/auth/forgot-password"), [{ email, turnstile_token: "token-1" }]);

    assert.equal(button("Request a new code").disabled, true);
    await solveChallenge("token-2");
    assert.equal(button("Request a new code").disabled, false);
    fireEvent.click(button("Request a new code"));
    await waitFor(() => assert.equal(bodies("/auth/forgot-password").length, 2));
    assert.deepEqual(bodies("/auth/forgot-password")[1], { email, turnstile_token: "token-2" });
    drawnFor("forgot_password");
});

test("forgot-password waits for the challenge before it can be submitted", () => {
    withKey();
    draw(<ForgotPasswordPage />, forgotQuery);
    assert.equal(button("Send reset code").disabled, true);
});

test("forgot-password without a site key is unchanged: no widget, no script, no token in the request", async () => {
    withoutKey();
    draw(<ForgotPasswordPage />, forgotQuery);
    assert.equal(button("Send reset code").disabled, false);
    fireEvent.click(button("Send reset code"));
    await screen.findByLabelText("Code");
    assert.deepEqual(bodies("/auth/forgot-password"), [{ email }]);
    assert.equal(button("Request a new code").disabled, false);
    assert.deepEqual(challenges, []);
    assert.equal(document.querySelectorAll(SCRIPT).length, 0);
});
