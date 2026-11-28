import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test, { afterEach, mock } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import { api, type MeResponse } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import { apiBaseUrl, env } from "@/lib/env";
import PricingPage from "./page";
import { signUpFor } from "./plan-cta";

// W2-T2 to T10: what the rendered pricing page says and where its buttons go.
const originalApiBase = env.NEXT_PUBLIC_API_BASE_URL;
const apiBase = "https://catalog.example.invalid/api/v1";
afterEach(() => { cleanup(); mock.restoreAll(); env.NEXT_PUBLIC_API_BASE_URL = originalApiBase; localStorage.clear(); });

const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

async function renderPricing(signedIn: boolean) {
    env.NEXT_PUBLIC_API_BASE_URL = apiBase;
    assert.equal(apiBaseUrl(), apiBase);
    mock.method(globalThis, "fetch", async (input: unknown) => {
        if (input === "/api/display-currency") return new Response(JSON.stringify({ currency: null }), { status: 200, headers: { "Content-Type": "application/json" } });
        return new Response(JSON.stringify({ plan: { id: "payg" }, packages: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    // A signed-in browser holds the stored session token (auth-token.ts); on a public page the account
    // is only read when one is there (auth-context.tsx:368-370).
    if (signedIn) localStorage.setItem("talklee.auth.token", "dummy");
    mock.method(api, "getMe", async () => {
        if (!signedIn) throw new ApiClientError({ status: 401, code: "unauthorized", message: "Sign in", url: "/auth/me", method: "GET" });
        return { id: "user-1", email: "owner@example.test", role: "tenant_admin", tenant_id: "tenant-1", minutes_remaining: 0 } as MeResponse;
    });
    let view!: ReturnType<typeof render>;
    await act(async () => {
        view = render(
            <AppRouterContext.Provider value={router}>
                <PathnameContext.Provider value="/pricing/">
                    <AuthProvider><ThemeProvider><PricingPage /></ThemeProvider></AuthProvider>
                </PathnameContext.Provider>
            </AppRouterContext.Provider>,
        );
    });
    return view;
}

const here = path.dirname(fileURLToPath(import.meta.url));
const pageSource = readFileSync(path.join(here, "page.tsx"), "utf8");
const payAsYouGoSource = readFileSync(path.join(here, "pay-as-you-go.tsx"), "utf8");

function sectionText(container: HTMLElement, label: string): string {
    const section = container.querySelector(`section[aria-label="${label}"]`);
    assert.ok(section, `the page has the section ${label}`);
    return section.textContent ?? "";
}
function headingSection(container: HTMLElement, heading: string): HTMLElement {
    const h2 = [...container.querySelectorAll("h2")].find((node) => node.textContent === heading);
    assert.ok(h2, `the page has the heading ${heading}`);
    return h2.closest("section") as HTMLElement;
}

test("W2-T2 an annual plan's price per month is computed and shown to the cent, never as a rounded dollar", async () => {
    const { container } = await renderPricing(false);
    const text = container.textContent ?? "";
    assert.ok(text.includes("$82.17/month"), "Basic annual per month");
    assert.ok(text.includes("$248.17/month"), "Business annual per month");
    assert.ok(text.includes("$986/year ($82.17/month)"), "comparison row, Basic");
    assert.ok(text.includes("$2,978/year ($248.17/month)"), "comparison row, Business");
    assert.doesNotMatch(text, /\$82\/month|\$248\/month/);
    assert.ok(text.includes("save 17%") && text.includes("Annual billing (17% off)"));
    // The per-month figures come from the catalogue, not from typed strings.
    assert.doesNotMatch(pageSource, /82\.17|248\.17|usdAnnualMonthly:\s*\d/);
});

test("W2-T1 the page shows the catalogue's plan figures and bundles", async () => {
    const { container } = await renderPricing(false);
    const text = container.textContent ?? "";
    for (const figure of ["$99/month", "$299/month", "$986/year", "$2,978/year", "300/month", "1,000/month", "$50", "125 minutes", "$70", "175 minutes", "$100", "250 minutes", "$0.40/min"]) {
        assert.ok(text.includes(figure), `the page shows ${figure}`);
    }
    for (const highlight of ["Concurrent calls: 3", "Concurrent calls: 10", "Campaigns: 2", "Campaigns: 10", "Voice recording storage: 2 GB", "Voice recording storage: 4 GB"]) {
        assert.ok(text.includes(highlight), highlight);
    }
});

test("W2-T3 minutes wording: only 'AI minutes' and 'Telephone (PSTN / SIP) usage'", async () => {
    const { container } = await renderPricing(false);
    const text = container.textContent ?? "";
    for (const variant of [new RegExp("Talk" + "lee", "i"), /AI-minute/i, /AI platform minutes/i, /platform minutes/i, /Telephone\/PSTN\/SIP/i, /PSTN\/SIP/i, /telephone minutes/i, /carrier (usage|charges|minutes)/i, /telephony/i, /billed separately/i]) {
        assert.doesNotMatch(text, variant, `the page must not say ${variant}`);
    }
    assert.doesNotMatch(text, /(?<!Telephone \()PSTN|PSTN(?! \/ SIP\) usage)/, "every PSTN is inside the one term");
    assert.ok(text.includes("Telephone (PSTN / SIP) usage is not included in AI minutes."));
    assert.ok(headingSection(container, "Telephone usage").textContent!.includes("Its cost depends on the phone line you connect."));
});

test("W2-T4 no unfinished, temporary or quoted wording on the page or in its sources", async () => {
    const { container } = await renderPricing(false);
    const forbidden = [/indicative/i, /client agreement/i, /coming soon/i, /temporar/i, /for now/i, /\bTBD\b/, /can be quoted/i, /quote/i, /custom pricing/i, /custom minute bundles/i, /custom carrier rates/i, /should be confirmed/i];
    for (const source of [container.textContent ?? "", pageSource, payAsYouGoSource]) {
        for (const pattern of forbidden) assert.doesNotMatch(source, pattern, `forbidden: ${pattern}`);
    }
});

test("W2-T5 ElevenLabs is one line: a separate premium add-on with its figure and no button", async () => {
    const { container } = await renderPricing(false);
    const list = headingSection(container, "Voice providers");
    const lines = [...list.querySelectorAll("li")].filter((li) => /ElevenLabs/.test(li.textContent ?? ""));
    assert.equal(lines.length, 1);
    assert.equal(lines[0].textContent, "• ElevenLabs voices are a separate premium add-on: $50/month.");
    assert.equal(lines[0].querySelectorAll("a, button").length, 0);
    assert.equal(list.querySelectorAll("a, button").length, 0);
    const mentions = [...container.querySelectorAll("li")].filter((li) => /ElevenLabs/.test(li.textContent ?? ""));
    assert.equal(mentions.length, 1, "no second ElevenLabs sentence anywhere on the page");
});

test("W2-T6 the top-up sentences say a top-up can be bought at any time", async () => {
    const { container } = await renderPricing(false);
    const topups = headingSection(container, "AI minute top-up bundles").textContent!;
    assert.match(topups, /With an active paid plan you can buy extra AI minutes at any time, at the standard rate of \$0\.40 per minute\./);
    const notes = headingSection(container, "Billing notes").textContent!;
    assert.match(notes, /you can buy a top-up at any time, not only when the included AI minutes have run out\./);
    assert.doesNotMatch(container.textContent!, /exhausted|before the next monthly renewal/i);
    assert.ok(!/larger custom|quoted/i.test(container.textContent!));
});

test("W2-T7 each plan states what the code does with recordings: a 90-day default setting, no automatic deletion", async () => {
    const { container } = await renderPricing(false);
    const sentence = "Recording retention setting: 90 days by default; recordings are not deleted automatically";
    const plans = sectionText(container, "Plans");
    assert.equal(plans.split(sentence).length - 1, 2, "both plan cards carry it");
    const rows = [...container.querySelectorAll("tr")].find((row) => row.querySelector("th")?.textContent === "Recording retention");
    assert.ok(rows);
    assert.equal(rows.textContent!.split(sentence).length - 1, 2, "both table cells carry it");
    assert.doesNotMatch(container.textContent!, /unless you change it|30 days|deleted after|deleted automatically after/i);
});

function planCard(container: HTMLElement, name: string): HTMLElement {
    const heading = [...container.querySelectorAll("section[aria-label=Plans] h2")].find((node) => node.textContent === name);
    assert.ok(heading, `card ${name}`);
    return heading.parentElement as HTMLElement;
}
// In this test environment next/link prints a path without its trailing slash. Next does not add it here
// (skipTrailingSlashRedirect is on): the proxy redirects a slash-less page address. The links are written with the slash
// (signUpFor, planPageFor) so the visitor is not redirected once more; those exact strings are asserted below.
const bare = (href: string | null) => (href ?? "").replace(/\/(\?|$)/, "$1");
const registerFor = (next: string) => bare(`/auth/register/?next=${encodeURIComponent(next)}`);

test("W2-T8 signed out, each plan button names its plan and goes to sign-up carrying the plan page with the chosen period", async () => {
    const { container } = await renderPricing(false);
    for (const name of ["Basic", "Business"]) {
        const link = within(planCard(container, name)).getByRole("link", { name: `Select ${name}` });
        assert.equal(link.getAttribute("href"), registerFor("/billing/plans/?interval=year"), `${name}: annual is on first paint`);
    }
    fireEvent.click(screen.getByRole("switch", { name: "Annual" }));
    for (const name of ["Basic", "Business"]) {
        const link = within(planCard(container, name)).getByRole("link", { name: `Select ${name}` });
        assert.equal(link.getAttribute("href"), registerFor("/billing/plans/?interval=month"), `${name}: monthly after the switch`);
    }
    fireEvent.click(screen.getByRole("switch", { name: "Annual" }));
    assert.equal(within(planCard(container, "Basic")).getByRole("link", { name: "Select Basic" }).getAttribute("href"), registerFor("/billing/plans/?interval=year"));
});

test("W2-T9 signed in, the same buttons go straight to the plan page", async () => {
    const { container } = await renderPricing(true);
    for (const name of ["Basic", "Business"]) {
        const link = within(planCard(container, name)).getByRole("link", { name: `Select ${name}` });
        await waitFor(() => assert.equal(bare(link.getAttribute("href")), "/billing/plans?interval=year"));
    }
    fireEvent.click(screen.getByRole("switch", { name: "Annual" }));
    assert.equal(bare(within(planCard(container, "Business")).getByRole("link", { name: "Select Business" }).getAttribute("href")), "/billing/plans?interval=month");
});

test("W2-T10 the Pay as you go card has a button: signed out to sign-up carrying the Billing page, signed in to Billing", async () => {
    const out = await renderPricing(false);
    const card = out.container.querySelector('section[aria-label="Pay as you go"]') as HTMLElement;
    assert.equal(within(card).getByRole("link", { name: "Buy minutes" }).getAttribute("href"), registerFor("/billing/"));
    cleanup();
    const signedIn = await renderPricing(true);
    const card2 = signedIn.container.querySelector('section[aria-label="Pay as you go"]') as HTMLElement;
    await waitFor(() => assert.equal(bare(within(card2).getByRole("link", { name: "Buy minutes" }).getAttribute("href")), "/billing"));
});

test("W2 the page starts no checkout of its own: its buttons are plain links to the dashboard's pages", async () => {
    const { container } = await renderPricing(false);
    assert.equal(container.querySelectorAll('section[aria-label="Plans"] button, section[aria-label="Pay as you go"] button').length, 0);
    assert.doesNotMatch(pageSource + readFileSync(path.join(here, "plan-cta.tsx"), "utf8"), /fetch\(|api\.|createCheckout|stripe|paypal/i);
});

test("L5 the signed-out links are written with the trailing slash, so the proxy does not redirect them again", () => {
    assert.equal(signUpFor("/billing/plans/?interval=year"), `/auth/register/?next=${encodeURIComponent("/billing/plans/?interval=year")}`);
    assert.equal(signUpFor("/billing/"), `/auth/register/?next=${encodeURIComponent("/billing/")}`);
});
