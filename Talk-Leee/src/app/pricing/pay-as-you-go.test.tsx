import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, screen } from "@testing-library/react";
import { loadPayAsYouGo, PayAsYouGoCard, type PaygBundle } from "./pay-as-you-go";

afterEach(() => cleanup());

const bundle: PaygBundle = { code: "payg_125", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2 };

function respond(status: number, body: unknown): typeof fetch {
  return (async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })) as typeof fetch;
}

test("the page shows the live pay-as-you-go bundles and their prices", () => {
  render(<PayAsYouGoCard bundles={[bundle, { ...bundle, code: "payg_250", minutes: 250, price_cents: 10000 }]} />);
  assert.ok(screen.getByText("Pay as you go"));
  assert.ok(screen.getByText("No monthly fee"));
  assert.ok(screen.getByText(/125 minutes: \$50\.00/));
  assert.ok(screen.getByText(/250 minutes: \$100\.00/));
});

test("without live prices the page sends people to Billing instead of inventing a price", () => {
  render(<PayAsYouGoCard bundles={[]} />);
  assert.ok(screen.getByText(/shown in Billing after you sign up/));
  assert.equal(screen.queryByText(/\$/), null);
});

test("prices are read from the backend and a failed or malformed answer yields none", async () => {
  assert.deepEqual(await loadPayAsYouGo(respond(200, { plan: { id: "payg" }, packages: [bundle] })), [bundle]);
  assert.deepEqual(await loadPayAsYouGo(respond(404, { detail: "Pay as you go is not offered." })), []);
  assert.deepEqual(await loadPayAsYouGo(respond(200, { packages: [{ code: "x", minutes: -1 }] })), []);
  assert.deepEqual(await loadPayAsYouGo((async () => { throw new Error("offline"); }) as typeof fetch), []);
});

test("while prices load the card says so and shows no price", () => {
  render(<PayAsYouGoCard bundles={null} />);
  assert.ok(screen.getByText(/Loading current prices/));
  assert.equal(screen.queryByText(/\$/), null);
});
