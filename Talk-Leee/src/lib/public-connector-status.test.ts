import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { PUBLIC_CONNECTORS } from "./public-connector-status";

// Source text of the dashboard page, read at run time (not imported: it is a
// client page that needs the whole app shell).
const page = readFileSync(path.join(process.cwd(), "src/app/connectors/page.tsx"), "utf8");
const block = page.slice(page.indexOf("const PROVIDERS"), page.indexOf("export default function"));
const cardNames = [...block.matchAll(/^\s+name: "([^"]+)"/gm)].map((m) => m[1]);

test("W5-T1 the public connector names equal the dashboard's card names, in the same order", () => {
    assert.ok(cardNames.length >= 5, `read the cards: ${cardNames.join(", ")}`);
    assert.deepEqual(PUBLIC_CONNECTORS.map((c) => c.name), cardNames);
});

test("W5-T1 the public list holds no connector the dashboard lacks", () => {
    for (const c of PUBLIC_CONNECTORS) assert.ok(cardNames.includes(c.name), `${c.name} is not a dashboard card`);
    assert.equal(new Set(PUBLIC_CONNECTORS.map((c) => c.name)).size, PUBLIC_CONNECTORS.length, "no duplicate");
});

test("W5-T2 each status follows owner decision O4", () => {
    const status = Object.fromEntries(PUBLIC_CONNECTORS.map((c) => [c.name, c.status]));
    assert.deepEqual(status, {
        "Google Calendar": "Limited",
        "Gmail": "Limited",
        "HubSpot": "Available",
        "Google Drive": "Limited",
        "Salesforce": "Limited",
    });
});

test("W5-T2 the sentences promise no more than the dashboard cards", () => {
    for (const c of PUBLIC_CONNECTORS) {
        assert.ok(c.does.trim() && c.customerMust.trim(), `${c.name} has both sentences`);
        assert.doesNotMatch(c.does + c.customerMust, /recordings?|reminders?|sync|automate|workflow|appointment/i, c.name);
    }
    const cal = PUBLIC_CONNECTORS.find((c) => c.name === "Google Calendar")!;
    assert.match(cal.customerMust, /not by calls/i, "the calendar is not used by calls");
});
