/**
 * FX6b Task 0.1 (plan 2.2: "No Buy button is enabled if the backend will
 * reject it."). When GET /billing/config says topups_open is false, every Buy
 * is disabled before a click whether or not a reason came with it. With a
 * reason the card shows it as before; without one it shows the general
 * sentence the card already had.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { TopupCard } from "@/components/billing/topup-card";
import { createAppQueryClient } from "@/lib/app-query-client";
import { AuthProvider } from "@/lib/auth-context";
import { resetBillingRefusal } from "@/lib/billing-refusal";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
const ME = { id: "user-a", email: "owner@example.test", role: "owner", tenant_id: "tenant-a", minutes_remaining: 20, minutes_state: "known" };
const BALANCE = { allocated: 1000, used_minutes: 20, remaining_minutes: 980, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 };
const BUNDLE = { code: "payg_125", name: "125 minutes", minutes: 125, price_cents: 5000, currency: "USD", currency_exponent: 2, expires_days: null, price_per_minute_cents: 40, audience: "payg" };
const SECOND = { ...BUNDLE, code: "payg_500", name: "500 minutes", minutes: 500, price_cents: 17500, price_per_minute_cents: 35 };
// The server's own sentence (backend/app/domain/services/billing_mode.py).
const TOPUPS_OFF = "Minute top-ups are not available yet.";
// The general sentence the card already had (topup-card.tsx).
const GENERAL = "No top-up bundles are available on your account right now. Contact the billing team for a review.";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let writes = 0;

function setup(config: unknown, packages: unknown[]) {
  writes = 0;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname.replace(/\/+$/, "");
    if (path.endsWith("/auth/me")) return json(ME);
    if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
    if ((init?.method ?? "GET") !== "GET") { writes += 1; return json({ detail: "unexpected write in this test" }, 500); }
    if (path.endsWith("/billing/config")) return json(config);
    if (path.endsWith("/balance")) return json(BALANCE);
    if (path.endsWith("/packages")) return json(packages);
    return json({ orders: [] });
  };
  notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
  const qc = createAppQueryClient(() => {});
  // Only the cache lifetime is changed: a finite gcTime keeps node --test alive after the tests.
  qc.setDefaultOptions({ ...qc.getDefaultOptions(), queries: { ...qc.getDefaultOptions().queries, gcTime: Infinity }, mutations: { ...qc.getDefaultOptions().mutations, gcTime: Infinity } });
  clients.push(qc);
  return render(<QueryClientProvider client={qc}><AuthProvider><SearchParamsContext.Provider value={new URLSearchParams("")}><TopupCard /></SearchParamsContext.Provider></AuthProvider></QueryClientProvider>);
}
const config = (topups_open: boolean, reason?: string | null) => ({ stripe_configured: true, mock_mode: false, billing_mode: "test", publishable_key: null,
  checkout_open: true, checkout_unavailable_reason: null, topups_open, ...(reason === undefined ? {} : { topups_unavailable_reason: reason }) });
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 60)));
const buys = () => screen.getAllByRole("button", { name: /^Buy / }) as HTMLButtonElement[];

afterEach(() => {
  cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); resetBillingRefusal();
  window.sessionStorage.clear(); window.localStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace(); notificationsStore.setIdentity(null);
});

for (const [name, reason] of [["a null reason", null], ["a blank reason", "   "], ["no reason field at all", undefined]] as Array<[string, string | null | undefined]>) {
  test(`top-ups closed with ${name} and bundles listed: every Buy is disabled before a click, with the general sentence once`, async () => {
    setup(config(false, reason), [BUNDLE, SECOND]);
    await screen.findByText(GENERAL);
    await settle();
    assert.deepEqual(buys().map((button) => button.disabled), [true, true], "every Buy is disabled before any click");
    assert.equal(screen.queryAllByText(GENERAL).length, 1, "the general sentence is shown once");
    assert.equal(screen.getByText(GENERAL).closest('[role="alert"]') !== null, true, "it stands where the server's reason would, above the bundles");
    fireEvent.click(buys()[0]!);
    await settle();
    assert.equal(writes, 0, "a disabled Buy starts no checkout");
  });
}

test("top-ups closed with a reason and bundles listed: Buy is disabled and the reason shows as before, not the general sentence", async () => {
  setup(config(false, TOPUPS_OFF), [BUNDLE, SECOND]);
  await screen.findByText(TOPUPS_OFF);
  await settle();
  assert.deepEqual(buys().map((button) => button.disabled), [true, true]);
  assert.equal(screen.queryAllByText(TOPUPS_OFF).length, 1);
  assert.equal(screen.queryAllByText(GENERAL).length, 0);
});

test("top-ups closed with no reason and no bundle: the general sentence, once, as before", async () => {
  setup(config(false, null), []);
  await screen.findByText(GENERAL);
  await settle();
  assert.equal(screen.queryAllByText(GENERAL).length, 1);
  assert.equal(screen.queryAllByRole("button", { name: /^Buy / }).length, 0);
});

test("top-ups open: Buy stays enabled and no sentence is added", async () => {
  setup(config(true, null), [BUNDLE]);
  const button = await screen.findByRole("button", { name: /^Buy / }) as HTMLButtonElement;
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(screen.queryAllByText(GENERAL).length, 0);
  assert.equal(screen.queryAllByRole("alert").length, 0);
});
