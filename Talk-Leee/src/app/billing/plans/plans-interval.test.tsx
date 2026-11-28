import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, screen } from "@testing-library/react";
import { api, sharedHttpClient } from "@/lib/api";
import { env } from "@/lib/env";
import { renderWithQueryClient } from "@/test-utils/render";
import { BillingPlanSelector, parseBillingInterval } from "@/components/billing/billing-plan-selector";

// W2-T15: the plan page starts on the billing period named in its address.
// The page (page.tsx) reads ?interval= and hands it to BillingPlanSelector as
// initialInterval; both halves are tested, and the page's wiring is read.
const MONTH = "b1336f1f-6927-4680-8a6b-7b7c86a06e89";
const YEAR = "bd8cb7f8-78ab-4ac8-bb15-24d9de9d34b0";
const monthly = { id: MONTH, kind: "stripe" as const, interval: "month" as const, interval_count: 1 as const, amount_minor: 1250, currency: "usd", currency_exponent: 2 as const, active: true, checkout_available: true, unavailable_reason: null };
const annual = { ...monthly, id: YEAR, interval: "year" as const, amount_minor: 13000 };
const plan = { id: "starter", name: "Starter", minutes: 100, concurrent_calls: 1, features: ["Calling"], billing_mode: "live", price_options: [monthly, annual] };
const emptySubscription = { status: "inactive", plan_id: null, plan_name: null, minutes_allocated: 0, minutes_used: 0, minutes_remaining: 0, billing_portal_available: false, purchased_price_option: null };

const originalRequest = api.request;
const oldBackend = env.NEXT_PUBLIC_API_BASE_URL;
const permissionsClient = sharedHttpClient();
const originalPermissionsRequest = permissionsClient.request;

afterEach(() => {
    cleanup();
    api.request = originalRequest;
    permissionsClient.request = originalPermissionsRequest;
    env.NEXT_PUBLIC_API_BASE_URL = oldBackend;
    mock.restoreAll();
});

function mount(initialInterval?: "month" | "year") {
    permissionsClient.request = (async () => ({ permissions: ["billing:read", "billing:update", "billing:admin"] })) as typeof permissionsClient.request;
    env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
    api.request = (async (opts: { path: string }) => (opts.path.includes("/billing/plans") ? [plan] : opts.path.includes("/billing/subscription") ? emptySubscription : {})) as typeof api.request;
    return renderWithQueryClient(<BillingPlanSelector scope="tenant-a:user-a" initialInterval={initialInterval} />);
}
const selected = () => (screen.getAllByRole("radio") as HTMLInputElement[]).find((radio) => radio.checked)?.value;

test("W2-T15 the addresses ?interval=month and ?interval=year name the period; anything else names none", () => {
    assert.equal(parseBillingInterval("month"), "month");
    assert.equal(parseBillingInterval("year"), "year");
    for (const bad of ["", "yearly", "YEAR", "Year", "week", "month ", "1", "../year", null, undefined]) assert.equal(parseBillingInterval(bad), null, String(bad));
});

test("W2-T15 the plan page starts on the year when the address says year", async () => {
    const view = mount("year");
    await screen.findByRole("radio", { name: "Yearly" });
    assert.equal(selected(), "year");
    view.qc.clear();
});

test("W2-T15 the plan page starts on the month when the address says month, and by default", async () => {
    mount("month");
    await screen.findByRole("radio", { name: "Yearly" });
    assert.equal(selected(), "month");
    cleanup();
    mount(undefined);
    await screen.findByRole("radio", { name: "Yearly" });
    assert.equal(selected(), "month", "no or a bad value gives the normal default");
});

test("W2-T15 the page reads ?interval= from its address and passes only a parsed value", async () => {
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
    assert.match(source, /useSearchParams\(\)/);
    assert.match(source, /parseBillingInterval\(searchParams\?\.get\("interval"\)\)/);
    assert.match(source, /<Suspense/);
});
