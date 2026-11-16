/**
 * FX6a Task 1 (plan 2.2: "No Buy button is enabled if the backend will reject
 * it."). GET /billing/config says before a click whether top-ups are closed
 * and gives the server's own reason. The card shows that reason in place of
 * the general "no bundles" sentence, turns every Buy off before a click when
 * a bundle is still listed, never shows the same reason twice, and behaves
 * exactly as before when the config read gives no answer.
 */
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { TopupCard } from "@/components/billing/topup-card";
import { createAppQueryClient } from "@/lib/app-query-client";
import { AuthProvider } from "@/lib/auth-context";
import { recordBillingRefusal, resetBillingRefusal } from "@/lib/billing-refusal";
import { clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
const ME = { id: "user-a", email: "owner@example.test", role: "owner", tenant_id: "tenant-a", minutes_remaining: 20, minutes_state: "known" };
const BALANCE = { allocated: 1000, used_minutes: 20, remaining_minutes: 980, unlimited: false, exhausted: false, purchased_minutes: 0, topup_minutes_left: 0 };
const BUNDLE = { code: "payg_125", name: "125 minutes", minutes: 125, price_cents: 5000, currency: "USD", currency_exponent: 2, expires_days: null, price_per_minute_cents: 40, audience: "payg" };
// The server's own sentences (backend/app/domain/services/billing_mode.py:78-80).
const TOPUPS_OFF = "Minute top-ups are not available yet.";
const TEAM_ONLY = "Payments are not open yet. Billing is in test mode for the Talk-Lee team.";
// The sentence the card showed before this read existed (topup-card.tsx).
const GENERAL = /No top-up bundles are available on your account right now/;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let reads: Record<string, number> = {};
let writes = 0;

// The app's own query client, so a query error would raise its real toast.
function mount() {
  notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
  const qc = createAppQueryClient(() => {});
  // Only the cache lifetime is changed: a finite gcTime keeps node --test alive after the tests.
  qc.setDefaultOptions({ ...qc.getDefaultOptions(), queries: { ...qc.getDefaultOptions().queries, gcTime: Infinity }, mutations: { ...qc.getDefaultOptions().mutations, gcTime: Infinity } });
  clients.push(qc);
  return render(<QueryClientProvider client={qc}><AuthProvider><SearchParamsContext.Provider value={new URLSearchParams("")}><TopupCard /></SearchParamsContext.Provider></AuthProvider></QueryClientProvider>);
}

function setup(config: Response | (() => Response), packages: unknown[] = []) {
  reads = {}; writes = 0;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname.replace(/\/+$/, "");
    reads[path] = (reads[path] ?? 0) + 1;
    if (path.endsWith("/auth/me")) return json(ME);
    if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
    if ((init?.method ?? "GET") !== "GET") { writes += 1; return json({ detail: "unexpected write in this test" }, 500); }
    if (path.endsWith("/billing/config")) return typeof config === "function" ? config() : config.clone();
    if (path.endsWith("/balance")) return json(BALANCE);
    if (path.endsWith("/packages")) return json(packages);
    return json({ orders: [] });
  };
  return mount();
}
const closed = (reason: string | null) => json({ stripe_configured: true, mock_mode: false, billing_mode: "test", publishable_key: null,
  checkout_open: true, checkout_unavailable_reason: null, topups_open: false, topups_unavailable_reason: reason });
const open = () => json({ stripe_configured: true, mock_mode: false, billing_mode: "test", publishable_key: null,
  checkout_open: true, checkout_unavailable_reason: null, topups_open: true, topups_unavailable_reason: null });
const configReads = () => Object.entries(reads).filter(([path]) => path.endsWith("/billing/config")).reduce((n, [, c]) => n + c, 0);
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 60)));
const toasts = () => notificationsStore.getSnapshot().toasts.map((t) => `${t.title}: ${t.message}`);

afterEach(() => {
  cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear()); resetBillingRefusal();
  window.sessionStorage.clear(); window.localStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace(); notificationsStore.setIdentity(null);
});

test("top-ups closed and no bundle listed: the server's reason is shown in place of the general sentence", async () => {
  setup(closed(TOPUPS_OFF));
  await screen.findByText(TOPUPS_OFF);
  assert.equal(screen.queryAllByText(TOPUPS_OFF).length, 1, "the reason is shown once");
  assert.equal(screen.queryByText(GENERAL) === null, true, "the general sentence is replaced");
  assert.equal(screen.queryAllByRole("button", { name: /^Buy / }).length, 0, "no Buy button is offered");
  assert.equal(configReads(), 1, "GET /billing/config is read once");
});

test("the general sentence is never shown first while the config read is still on its way", async () => {
  let answer: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => { answer = resolve; });
  reads = {};
  globalThis.fetch = async (input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = new URL(url).pathname.replace(/\/+$/, "");
    if (path.endsWith("/auth/me")) return json(ME);
    if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
    if (path.endsWith("/billing/config")) return pending;
    if (path.endsWith("/balance")) return json(BALANCE);
    if (path.endsWith("/packages")) return json([]);
    return json({ orders: [] });
  };
  mount();
  await screen.findByText("No saved top-up orders recorded.");
  await settle();
  assert.equal(screen.queryByText(GENERAL) === null, true, "no general sentence before the config answer");
  assert.equal(screen.queryByText(/Loading bundles/) !== null, true, "the card is still loading");
  await act(async () => { answer(closed(TEAM_ONLY)); });
  await screen.findByText(TEAM_ONLY);
  assert.equal(screen.queryByText(GENERAL) === null, true);
});

test("a bundle still listed while top-ups are closed: every Buy is off before a click, with the server's reason once", async () => {
  setup(closed(TOPUPS_OFF), [BUNDLE, { ...BUNDLE, code: "payg_500", name: "500 minutes", minutes: 500, price_cents: 17500, price_per_minute_cents: 35 }]);
  await screen.findByText(TOPUPS_OFF);
  const buttons = screen.getAllByRole("button", { name: /^Buy / }) as HTMLButtonElement[];
  assert.equal(buttons.length, 2);
  assert.deepEqual(buttons.map((button) => button.disabled), [true, true], "every Buy is disabled before any click");
  assert.equal(screen.queryAllByText(TOPUPS_OFF).length, 1, "the reason is shown once");
  assert.equal(screen.getByText(TOPUPS_OFF).closest('[role="alert"]') !== null, true, "the reason sits in the alert above the bundles");
  fireEvent.click(buttons[0]);
  await settle();
  assert.equal(writes, 0, "a disabled Buy starts no checkout");
});

test("the same reason is not repeated when a refusal after a click said it without the full stop", async () => {
  // The 503 from POST /billing/topups/checkout ends without a full stop; the config reason ends with one.
  recordBillingRefusal({ code: "topups_unavailable", message: "Minute top-ups are not available yet", scope: "topup" });
  setup(closed(TOPUPS_OFF));
  await screen.findByText(TOPUPS_OFF);
  await settle();
  assert.equal(screen.queryAllByText(/Minute top-ups are not available yet/).length, 1, "one reason, not two spellings of it");
  assert.equal(screen.queryAllByRole("alert").length, 0, "no second box repeats the reason");
});

test("the same reason is not repeated above listed bundles either", async () => {
  recordBillingRefusal({ code: "topups_unavailable", message: "Minute top-ups are not available yet", scope: "topup" });
  setup(closed(TOPUPS_OFF), [BUNDLE]);
  await screen.findByText(TOPUPS_OFF);
  assert.equal(screen.queryAllByText(/Minute top-ups are not available yet/).length, 1);
  assert.equal((screen.getByRole("button", { name: /^Buy / }) as HTMLButtonElement).disabled, true);
});

test("a different refusal recorded after a click is still shown beside the closed reason", async () => {
  recordBillingRefusal({ code: "tenant_unavailable", message: "Synthetic account refusal", scope: "all" });
  setup(closed(TOPUPS_OFF));
  await screen.findByText(TOPUPS_OFF);
  assert.equal(screen.queryAllByText("Synthetic account refusal").length, 1, "a different refusal is not hidden");
});

test("top-ups open: bundles are offered with Buy enabled and no reason is shown", async () => {
  setup(open(), [BUNDLE]);
  const button = await screen.findByRole("button", { name: /^Buy / }) as HTMLButtonElement;
  await settle();
  assert.equal(button.disabled, false);
  assert.equal(screen.queryAllByRole("alert").length, 0);
});

// FX6b Task 0.1: "closed" with no reason is still an answer. With a bundle
// listed every Buy is off and the general sentence stands where the reason
// would (before FX6b these two cases left Buy enabled).
for (const [name, config, saysClosed] of [
  ["a failed config read (503)", () => json({ detail: "Synthetic unavailable" }, 503), false],
  ["a config answer without the expected fields", () => json({ stripe_configured: true }), false],
  ["a config answer that says closed but gives no reason", () => closed(null), true],
  ["a config answer that says closed with a blank reason", () => closed("   "), true],
] as Array<[string, () => Response, boolean]>) {
  test(`${name}: with no bundle the card shows the general sentence exactly as before`, async () => {
    setup(config);
    await screen.findByText(GENERAL);
    await settle();
    assert.equal(screen.queryAllByRole("alert").length, 0, "no reason box is made up");
    assert.deepEqual(toasts(), [], "the optional read raises no error toast");
  });

  test(saysClosed
    ? `${name}: with a bundle listed every Buy is off and the general sentence is shown once`
    : `${name}: with a bundle listed Buy stays enabled exactly as before`, async () => {
    setup(config, [BUNDLE]);
    const button = await screen.findByRole("button", { name: /^Buy / }) as HTMLButtonElement;
    await settle();
    assert.equal(button.disabled, saysClosed);
    assert.equal(screen.queryAllByRole("alert").length, saysClosed ? 1 : 0);
    assert.equal(screen.queryAllByText(GENERAL).length, saysClosed ? 1 : 0);
    assert.deepEqual(toasts(), [], "the optional read raises no error toast");
  });
}
