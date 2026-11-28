import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  CATALOG_PLANS,
  CATALOG_TOPUPS,
  annualPerMonthCents,
  annualSavingPercent,
  bundleRateUsd,
  formatUsd,
  pageSavingPercent,
} from "./catalog";

// W2-T1 and W2-T2. The backend is the truth for every figure on the pricing
// page; the three files are read at run time, so a changed price there fails
// this test until the page's catalogue follows.
const here = path.dirname(fileURLToPath(import.meta.url));
const backend = path.resolve(here, "..", "..", "..", "..", "backend");
const readBackend = (relative: string) => {
  const file = path.join(backend, relative);
  assert.ok(existsSync(file), `the backend file must be readable from the website tests: ${relative}`);
  return readFileSync(file, "utf8");
};

const stripe = readBackend("scripts/stripe_catalog_setup.py");
const paid = readBackend("Alembic/versions/0071_paid_only_billing.py");
const payg = readBackend("Alembic/versions/0083_pay_as_you_go.py");

/** {"month": 9900, "year": 98600} of one CATALOG entry, as numbers. */
function stripePrices(planId: string): { month: number; year: number } {
  const entry = new RegExp(`"${planId}":\\s*\\("[^"]*",\\s*\\{([^}]*)\\}\\)`).exec(stripe);
  assert.ok(entry, `CATALOG has an entry for ${planId}`);
  const price = (interval: string) => {
    const match = new RegExp(`"${interval}":\\s*(\\d+)`).exec(entry[1]);
    assert.ok(match, `CATALOG ${planId} has a ${interval} price`);
    return Number(match[1]);
  };
  return { month: price("month"), year: price("year") };
}

/** The numbers of `UPDATE ... SET name = 'Basic', price = 99, ... minutes = 300, agents = 2, concurrent_calls = 3`. */
function basicRow() {
  const match = /SET name = 'Basic', price = (\d+),[\s\S]*?minutes = (\d+), agents = (\d+),\s*concurrent_calls = (\d+)/.exec(paid);
  assert.ok(match, "0071 updates the Basic plan row");
  return { price: Number(match[1]), minutes: Number(match[2]), agents: Number(match[3]), concurrent: Number(match[4]) };
}
/** `VALUES ('business', 'Business', 299, '...', 1000, 10, 10,`. */
function businessRow() {
  const match = /VALUES \('business', 'Business', (\d+),\s*'[^']*',\s*(\d+),\s*(\d+),\s*(\d+),/.exec(paid);
  assert.ok(match, "0071 inserts the Business plan row");
  return { price: Number(match[1]), minutes: Number(match[2]), agents: Number(match[3]), concurrent: Number(match[4]) };
}
/** `Voice recording storage: 2 GB` in the plan's feature text (the only place the backend names it). */
function storageGb(featuresName: "BASIC_FEATURES" | "BUSINESS_FEATURES"): number {
  const block = new RegExp(`${featuresName} = \\(([\\s\\S]*?)\\n\\)`).exec(paid);
  assert.ok(block, `0071 defines ${featuresName}`);
  const match = /Voice recording storage: (\d+) GB/.exec(block[1]);
  assert.ok(match, `${featuresName} names the storage`);
  return Number(match[1]);
}

test("W2-T1 each plan's monthly and yearly price equals the Stripe catalogue (cents)", () => {
  for (const plan of CATALOG_PLANS) {
    const prices = stripePrices(plan.id);
    assert.equal(plan.monthlyCents, prices.month, `${plan.id} monthly`);
    assert.equal(plan.annualCents, prices.year, `${plan.id} yearly`);
  }
});

test("W2-T1 each plan's dollar price, minutes, calls at once and campaigns equal migration 0071", () => {
  const rows = { basic: basicRow(), business: businessRow() };
  for (const plan of CATALOG_PLANS) {
    const row = rows[plan.id];
    assert.equal(plan.monthlyCents, row.price * 100, `${plan.id} price`);
    assert.equal(plan.minutes, row.minutes, `${plan.id} included minutes`);
    assert.equal(plan.concurrentCalls, row.concurrent, `${plan.id} calls at once`);
    assert.equal(plan.campaigns, row.agents, `${plan.id} campaigns (the backend column is "agents")`);
  }
  assert.equal(CATALOG_PLANS[0].storageGb, storageGb("BASIC_FEATURES"));
  assert.equal(CATALOG_PLANS[1].storageGb, storageGb("BUSINESS_FEATURES"));
});

test("W2-T1 the top-up bundles equal the usd_125 / usd_175 / usd_250 rows of migration 0071 and the payg rows of 0083", () => {
  const rows = (source: string, prefix: string) =>
    [...source.matchAll(new RegExp(`\\('${prefix}_(\\d+)', '[^']*', (\\d+), (\\d+), 'USD'`, "g"))].map((m) => ({
      code: Number(m[1]),
      minutes: Number(m[2]),
      priceCents: Number(m[3]),
    }));
  const topups = rows(paid, "usd");
  const paygRows = rows(payg, "payg");
  assert.equal(topups.length, CATALOG_TOPUPS.length);
  assert.equal(paygRows.length, CATALOG_TOPUPS.length);
  CATALOG_TOPUPS.forEach((bundle, index) => {
    assert.equal(topups[index].code, topups[index].minutes, "the code names the minutes");
    assert.equal(bundle.minutes, topups[index].minutes, `bundle ${index} minutes`);
    assert.equal(bundle.priceCents, topups[index].priceCents, `bundle ${index} price`);
    assert.equal(bundle.minutes, paygRows[index].minutes, `pay as you go bundle ${index} minutes`);
    assert.equal(bundle.priceCents, paygRows[index].priceCents, `pay as you go bundle ${index} price`);
    assert.equal(bundleRateUsd(bundle) * 100 * bundle.minutes, bundle.priceCents, "the per-minute rate is exact");
  });
  assert.deepEqual(new Set(CATALOG_TOPUPS.map(bundleRateUsd)), new Set([0.4]), "every bundle is $0.40 a minute");
});

test("W2-T2 the price per month of an annual plan is the yearly total over twelve months, to the cent", () => {
  assert.equal(formatUsd(annualPerMonthCents(98600)), "$82.17");
  assert.equal(formatUsd(annualPerMonthCents(297800)), "$248.17");
  // Fixed expected numbers (cents): 98600 / 12 = 8216.67 and 297800 / 12 = 24816.67, each rounded to the cent.
  assert.equal(annualPerMonthCents(98600), 8217);
  assert.equal(annualPerMonthCents(297800), 24817);
  assert.deepEqual(CATALOG_PLANS.map((plan) => annualPerMonthCents(plan.annualCents)), [8217, 24817]);
});

test("W2-T2 the stated saving equals the real saving of each plan", () => {
  assert.equal(pageSavingPercent(), 17);
  for (const plan of CATALOG_PLANS) {
    const real = 1 - plan.annualCents / (plan.monthlyCents * 12);
    assert.ok(Math.abs(real * 100 - pageSavingPercent()) < 0.5, `${plan.id} really saves ${(real * 100).toFixed(2)}%`);
    assert.equal(annualSavingPercent(plan), pageSavingPercent());
  }
});

test("W2-T2 whole dollars carry no cents, other amounts carry two", () => {
  assert.equal(formatUsd(9900), "$99");
  assert.equal(formatUsd(297800), "$2,978");
  assert.equal(formatUsd(8217), "$82.17");
});

// W2 review L2. The three backend files above are the ones this test knows. A later migration that changes a
// price, an included-minutes figure or a top-up bundle would make the page wrong without failing anything, so
// the migration folder is read at run time and any such change fails here.
const versionsDir = path.join(backend, "Alembic", "versions");
// Allowed after 0083, by name: 0097 edits the plan card's feature TEXT only (it removes one line from `features` and
// `not_included`); it writes no price and no minutes.
const ALLOWED_AFTER_0083 = new Set(["0097_remove_campaign_wizard_plan_line.py"]);
const MONEY_WRITES: [string, RegExp][] = [
  ["mentions topup_packages", /topup_packages/i],
  ["writes plan_price_options", /(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:public\.)?plan_price_options\b/i],
  ["inserts a plan row", /INSERT\s+INTO\s+(?:public\.)?plans\b/i],
  ["updates a plan's price or minutes", /UPDATE\s+(?:public\.)?plans\s+SET[^;]*\b(?:price|price_cents|minutes)\b/i],
];

test("W2 L2 no migration after 0083 changes a plan price, a plan's minutes or a top-up bundle", () => {
  const files = readdirSync(versionsDir).filter((name) => /^\d{4}_.*\.py$/.test(name)).sort();
  assert.ok(files.some((name) => name.startsWith("0083_")), "the folder is read, and 0083 is in it");
  const offenders: string[] = [];
  for (const name of files) {
    if (Number(name.slice(0, 4)) <= 83 || ALLOWED_AFTER_0083.has(name)) continue;
    const text = readFileSync(path.join(versionsDir, name), "utf8");
    for (const [what, pattern] of MONEY_WRITES) if (pattern.test(text)) offenders.push(`${name} ${what}`);
  }
  assert.deepEqual(
    offenders,
    [],
    "A migration after 0083 changes what this page shows. Update catalog.ts (src/app/pricing) and this test together, " +
      "then add the migration to ALLOWED_AFTER_0083 only if it changes neither a price nor minutes.",
  );
});

test("W2 L2 the guard can see a money write: a sample line is caught, the 0097 feature-text edit is not", () => {
  const sample = "op.execute(\"UPDATE plans SET price = 129 WHERE id = 'basic'\")";
  assert.ok(MONEY_WRITES.some(([, pattern]) => pattern.test(sample)));
  const text0097 = readFileSync(path.join(versionsDir, "0097_remove_campaign_wizard_plan_line.py"), "utf8");
  assert.ok(!MONEY_WRITES.some(([, pattern]) => pattern.test(text0097)), "0097 is allowed on its merits, not only by name");
});
