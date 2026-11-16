import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import { trailingSlashRedirectPath } from "@/proxy";
import VerifyEmailPage from "./page";

// The page the "verify your email" link opens. The token arrives in the query,
// is checked exactly once, leaves the address bar once the server has answered,
// and is never shown.
const TOKEN = "v".repeat(43);
const SLOW = 15_000;
const NEUTRAL = "If that address needs verifying, we have sent a new email.";

afterEach(() => { cleanup(); mock.restoreAll(); window.history.replaceState(null, "", "/"); });

function refusal(status: number, message = "refused") {
    return new ApiClientError({ status, code: "http_error", message, url: "http://example.test/auth/verify-email", method: "GET" });
}

type Call = { path: string; method?: string; query?: Record<string, unknown>; params?: Record<string, unknown>; body?: unknown };

function wire(handlers: { verify?: () => Promise<unknown>; resend?: () => Promise<unknown> }) {
    const calls: Call[] = [];
    mock.method(api, "request", async (opts: Call) => {
        calls.push(opts);
        if (opts.path === "/auth/verify-email/resend") return handlers.resend ? handlers.resend() : { message: NEUTRAL };
        if (opts.path === "/auth/verify-email") return handlers.verify ? handlers.verify() : { message: "Email verified successfully! You can now log in.", email: "sam@example.com" };
        throw new Error(`unexpected ${opts.path}`);
    });
    return {
        verifies: () => calls.filter((c) => c.path === "/auth/verify-email"),
        resends: () => calls.filter((c) => c.path === "/auth/verify-email/resend"),
    };
}

function openLink(search: string, strict = false) {
    window.history.replaceState(null, "", `/auth/verify-email${search}`);
    return render(strict ? <React.StrictMode><VerifyEmailPage /></React.StrictMode> : <VerifyEmailPage />);
}

test("a good link verifies the email once, says so, points to sign-in and leaves the address bar", async () => {
    const seen = wire({});
    openLink(`?token=${TOKEN}`);
    await screen.findByText("Your email is verified. You can now sign in.", undefined, { timeout: SLOW });
    assert.equal(seen.verifies().length, 1);
    assert.deepEqual(seen.verifies()[0]!.query ?? seen.verifies()[0]!.params, { token: TOKEN });
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), "/auth/login");
    assert.equal(window.location.search, "");
    assert.equal(window.location.href.includes(TOKEN), false);
    assert.equal((document.body.textContent ?? "").includes(TOKEN), false);
});

test("an expired or already used link says so and offers a new one", async () => {
    for (const status of [404, 410]) {
        const seen = wire({ verify: async () => { throw refusal(status); } });
        openLink(`?token=${TOKEN}`);
        await screen.findByText("This link has expired or was already used.", undefined, { timeout: SLOW });
        assert.equal(screen.queryByLabelText("Email") !== null, true);
        assert.equal(screen.queryByRole("button", { name: "Send a new link" }) !== null, true);
        assert.equal(seen.verifies().length, 1);
        cleanup();
        mock.restoreAll();
    }
});

test("a link without its token asks nothing of the server and offers the new-link form", async () => {
    const seen = wire({});
    openLink("");
    await screen.findByRole("button", { name: "Send a new link" }, { timeout: SLOW });
    assert.equal(seen.verifies().length, 0);
});

test("sending a new link shows the server's neutral sentence and never says whether the address exists", async () => {
    const seen = wire({});
    openLink("");
    fireEvent.change(await screen.findByLabelText("Email", undefined, { timeout: SLOW }), { target: { value: "  Sam@Example.com " } });
    fireEvent.click(screen.getByRole("button", { name: "Send a new link" }));
    await screen.findByText(NEUTRAL, undefined, { timeout: SLOW });
    assert.equal(seen.resends().length, 1);
    assert.deepEqual(seen.resends()[0]!.body, { email: "sam@example.com" });
});

test("too many requests shows the wait sentence", async () => {
    wire({ resend: async () => { throw refusal(429, "Too Many Requests"); } });
    openLink("");
    fireEvent.change(await screen.findByLabelText("Email", undefined, { timeout: SLOW }), { target: { value: "sam@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send a new link" }));
    await screen.findByText("Too many attempts. Wait a moment and try again.", undefined, { timeout: SLOW });
});

test("React mounting the page twice checks the token once and a shown success stays", async () => {
    const seen = wire({});
    openLink(`?token=${TOKEN}`, true);
    await screen.findByText("Your email is verified. You can now sign in.", undefined, { timeout: SLOW });
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(seen.verifies().length, 1);
    await waitFor(() => assert.equal(screen.queryByText("This link has expired or was already used.") === null, true));
    assert.equal(screen.queryByText("Your email is verified. You can now sign in.") !== null, true);
});

// Guards, not behaviour: src/proxy.ts already lets every /auth/* page through
// without a session; these are the two lists that name auth screens one by one.
test("the page and its two API paths are listed as sign-in-free", () => {
    const read = (file: string) => readFileSync(path.join(process.cwd(), file), "utf8");
    assert.match(read("src/lib/http-client.ts"), /PUBLIC_AUTH_PATHS = new Set\(\[[^\]]*"\/auth\/verify-email", "\/auth\/verify-email\/resend"/);
    assert.match(read("src/components/providers/app-providers.tsx"), /AUTH_FLOW_PATHS = \[[^\]]*"\/auth\/verify-email"/);
    assert.equal(trailingSlashRedirectPath("/auth/verify-email"), "/auth/verify-email/");
});
