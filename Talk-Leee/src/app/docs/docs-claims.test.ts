import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { docsPages } from "./_content/registry";
import { PUBLIC_CONNECTORS } from "../../lib/public-connector-status";

// W6: the public documentation says only what the product does. A guard over the 13 PUBLISHED pages
// (the list comes from registry.ts at run time; support.md and partners.md are withheld and not read),
// the registry descriptions and the index page. One test per rule, so a failure names the rule, the
// file and the sentence. Each sentence rule has a fixture test: the false sentences it must flag and
// an honest one it must let through. The rules are ported from src/app/industries/industry-claims.test.ts
// (R8 calendar, R9 transfer, R14 callbacks, R16 Business plan).

const root = process.cwd();
const read = (...parts: string[]) => fs.readFileSync(path.join(root, ...parts), "utf8");

const pages = docsPages.map((page) => ({ name: page.file, text: read("src", "app", "docs", "_content", page.file) }));
const descriptions = docsPages.map((page) => ({ name: `registry.ts (${page.slug} description)`, text: page.description }));
const indexPage = { name: "page.tsx", text: read("src", "app", "docs", "page.tsx") };
const everything = [...pages, ...descriptions, indexPage];

const pageText = (file: string) => pages.find((page) => page.name === file)?.text ?? "";

/**
 * Sentence-like chunks of a Markdown file. Headings, images and table rules are labels, not claims,
 * so they are skipped. A table row stays ONE chunk (its cells belong together). The `**bold**`
 * text is kept here; `unbold` masks it, because a bold word is a label of the screen ("Transfer to a
 * human" is a button name), and a rule about a claim must not fire on a label alone.
 */
function sentencesOf(text: string): string[] {
    const out: string[] = [];
    for (const raw of text.split(/\r?\n/)) {
        const line = raw
            .trim()
            .replace(/^>\s*/, "")
            .replace(/^(?:[-*]|\d+\.)\s+/, "");
        if (!line || /^#{1,6}\s/.test(line) || /^!\[/.test(line) || /^\|[\s|:-]+\|?$/.test(line)) continue;
        const plain = line.replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
        if (plain.startsWith("|")) {
            out.push(plain);
            continue;
        }
        for (const part of plain.split(/(?<=[.!?])\s+/)) if (part.trim()) out.push(part.trim());
    }
    return out;
}
const unbold = (sentence: string) => sentence.replace(/\*\*[^*]*\*\*/g, "·");

type Source = { name: string; text: string };
function sentenceHits(trigger: RegExp, allowed: RegExp, sources: Source[] = everything): string[] {
    return sources.flatMap((source) =>
        sentencesOf(source.text)
            .filter((sentence) => trigger.test(unbold(sentence)) && !allowed.test(sentence))
            .map((sentence) => `${source.name}: "${sentence}"`));
}
const hits = (rule: RegExp, sources: Source[] = everything): string[] =>
    sources.flatMap((source) => {
        const hit = source.text.match(rule);
        return hit ? [`${source.name}: "${hit[0]}"`] : [];
    });
const flagged = (trigger: RegExp, allowed: RegExp, sentence: string) => trigger.test(unbold(sentence)) && !allowed.test(sentence);

// ---------------------------------------------------------------------------------------------
// W6-T1: every dashboard menu name used in the docs is the real menu name.
// The real names are read from src/components/layout/sidebar.tsx at run time. A menu name is
// recognised in the Markdown in three ways: (1) bold text in "Select/Open **X** from the sidebar";
// (2) the bold names in the bullets of getting-started.md "### Main Navigation"; (3) any bold phrase
// that is a near miss of a top-level menu name ("Billing and Logs", "Call history": same words with
// other spelling, case or plural).
const sidebarSource = read("src", "components", "layout", "sidebar.tsx");
const navBlock = sidebarSource.slice(sidebarSource.indexOf("const navigation"), sidebarSource.indexOf("export function Sidebar"));
const MENU_ALL = [...navBlock.matchAll(/\bname:\s*"([^"]+)"/g)].map((match) => match[1]);
const MENU_CHILDREN = new Set([...navBlock.matchAll(/^\s{12,}\{\s*name:\s*"([^"]+)"/gm)].map((match) => match[1]));
const MENU_TOP = MENU_ALL.filter((name) => !MENU_CHILDREN.has(name));
const MENU_RESTRICTED = new Set(
    [...navBlock.matchAll(/\bname:\s*"([^"]+)"[^\n]*\b(?:adminOnly|supportOnly):\s*true/g)].map((match) => match[1]),
);
const squash = (name: string) => name.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]/g, "");
// The Settings tab names are read from the screen. They are tab labels, never menu names.
const settingsTabs = [...read("src", "app", "settings", "page.tsx").matchAll(/<TabsTrigger value="[^"]+"[^>]*>([^<]+)<\/TabsTrigger>/g)].map((match) => match[1]);

// "Select **X**" / "Open **X**" WITHOUT "from the sidebar" names a menu entry only when X is a near
// miss of one: the same letters with a plural or a shortened form ("Inbound" for "Inbound Campaigns",
// "Contact" for "Contacts"), or the same first word plus a word that names a page ("Call Logs" for
// "Call History"). A button, a tab or a column label ("Add contact", "Call anytime", "Security" tab,
// "Remove") is none of these, so it is left alone. The test is case-insensitive.
const PAGE_NOUNS = new Set(["logs", "log", "history", "list", "page", "area", "section", "campaigns", "campaign", "calls", "call", "reports"]);
// Button labels of a connector card (src/components/connectors/connector-card.tsx): "Connect" is the start of
// "Connectors" but it is the card's button, not a menu entry.
const CONNECTOR_BUTTONS = ["Connect", "Reconnect", "Disconnect"];
function isNearMiss(label: string): boolean {
    if (MENU_ALL.includes(label) || settingsTabs.includes(label) || CONNECTOR_BUTTONS.includes(label)) return false;
    const key = squash(label);
    const keyNoS = key.replace(/s$/, "");
    const words = label.toLowerCase().split(/\s+/);
    return MENU_TOP.some((menu) => {
        const menuKey = squash(menu);
        if (menuKey === key) return true; // same letters, other case or spelling
        if (key.length >= 4 && (menuKey.startsWith(key) || menuKey === keyNoS || menuKey.startsWith(keyNoS))) return true;
        const menuWords = menu.toLowerCase().split(/\s+/);
        return words.length > 1 && menuWords.length > 1 && words[0] === menuWords[0] && PAGE_NOUNS.has(words[words.length - 1]);
    });
}

function menuNamesUsed(text: string): string[] {
    const names = new Set<string>();
    for (const match of text.matchAll(/(?:Select|Open|Use) \*\*([^*]+)\*\*(?: from the (?:left )?sidebar| and open \*\*([^*]+)\*\*)/g)) {
        names.add(match[1]);
        if (match[2]) names.add(match[2]);
    }
    for (const match of text.matchAll(/\b(?:select|open|choose|use|go to)\s+\*\*([^*]+)\*\*/gi)) {
        if (isNearMiss(match[1])) names.add(match[1]);
    }
    const nav = /### Main Navigation\n([\s\S]*?)\n#{2,3} /.exec(text)?.[1] ?? "";
    for (const match of nav.matchAll(/^- \*\*([^*]+)\*\*/gm)) names.add(match[1]);
    for (const match of text.matchAll(/\*\*([^*]+)\*\*/g)) {
        const bold = match[1];
        if (MENU_TOP.some((menu) => menu !== bold && squash(menu) === squash(bold))) names.add(bold);
    }
    return [...names];
}
const notMenuNames = (text: string) => menuNamesUsed(text).filter((name) => !MENU_ALL.includes(name) && name !== "Logout");

test("W6-T1 the menu names in the docs are the sidebar's", () => {
    assert.ok(MENU_TOP.length >= 12, "the sidebar names were read");
    const problems = pages.flatMap((page) => notMenuNames(page.text).map((name) => `${page.name}: "${name}" is not a menu name`));
    assert.deepEqual(problems, []);
});
test("W6-T1 the Main Navigation list is the customer's menu, in the sidebar's order", () => {
    const nav = /### Main Navigation\n([\s\S]*?)\n#{2,3} /.exec(pageText("getting-started.md"))?.[1] ?? "";
    const listed = [...nav.matchAll(/^- \*\*([^*]+)\*\*/gm)].map((match) => match[1]);
    const expected = [...MENU_TOP.filter((name) => !MENU_RESTRICTED.has(name)), "Logout"];
    assert.deepEqual(listed, expected);
});
test("W6-T1 fixture: a near miss or an unknown menu name is flagged, the real name passes", () => {
    assert.deepEqual(notMenuNames("Select **Billing and Logs** from the sidebar."), ["Billing and Logs"]);
    assert.deepEqual(notMenuNames("Select **Calls** from the left sidebar."), ["Calls"]);
    assert.deepEqual(notMenuNames("Open the **Call history** page."), ["Call history"]);
    assert.deepEqual(notMenuNames("Select **Billing & Logs** and open **Billing**."), []);
    assert.deepEqual(notMenuNames("Select **Call History** from the sidebar."), []);
    assert.deepEqual(notMenuNames("Select **Edit** on the card."), []);
    // Without "from the sidebar": a near miss is flagged, in any case; buttons, tabs and labels pass.
    assert.deepEqual(notMenuNames("In the sidebar, select **Inbound** to create a campaign."), ["Inbound"]);
    assert.deepEqual(notMenuNames("Open **Call Logs** to review past calls."), ["Call Logs"]);
    assert.deepEqual(notMenuNames("open **call history** to review calls."), ["call history"]);
    assert.deepEqual(notMenuNames("Select **Contact** to add people."), ["Contact"]);
    assert.deepEqual(notMenuNames("Select **Call History** to review calls."), []);
    assert.deepEqual(notMenuNames("Select **Add contact**, then select **Call anytime** and **Remove**."), []);
    assert.deepEqual(notMenuNames("Open **Settings → Recording**."), []);
    assert.deepEqual(notMenuNames("Select the **Security** tab, then **Save policy**."), []);
});

// ---------------------------------------------------------------------------------------------
// W6-T2: Security Center and Agent Quality are admin-only (owner decision O5). Agent Reviews sits
// under Agent Quality, so it is described the same way.
// The sentence is read WITH its bold names (the screen names are the subject here, so `unbold` is not used).
const ADMIN_AREAS = /\bSecurity Center\b|\bAgent Quality\b/;
// What an honest sentence says: the areas are not in a customer account (the older wording, "platform and
// partner administrators", is accepted too). The bare word "administrator" is NOT enough.
const SAYS_ADMIN_ONLY =
    /\bplatform and partner administrators\b|\bnot shown in a customer account\b|\badmin-only\b|\b(?:cannot|can't|do not|does not|never) (?:see|open)\b/i;
// "Account administrators can open Security Center": a customer-side administrator who can open the area.
const CUSTOMER_ADMIN_OPENS =
    /\b(?:customer|account|tenant)(?: account)? (?:administrators?|admins?)\b[^.]{0,60}\b(?:can|may|could|are able to|is able to|see|sees|open|opens|access|accesses)\b/i;
const NEGATED = /\b(?:not|never|cannot|can't|no)\b|n't\b/i;
const DEPENDS_ON_PLAN = /\b(?:depend\w*|based) on your plan\b|\byour plan and role\b|\bplan and role\b/i;
const adminAreaProblem = (s: string) =>
    ADMIN_AREAS.test(s) && (!SAYS_ADMIN_ONLY.test(s) || (CUSTOMER_ADMIN_OPENS.test(s) && !NEGATED.test(s)) || DEPENDS_ON_PLAN.test(s));
test("W6-T2 Security Center and Agent Quality are not in a customer account, never 'depending on your plan'", () => {
    const problems = pages.flatMap((page) => sentencesOf(page.text).filter(adminAreaProblem).map((s) => `${page.name}: "${s}"`));
    assert.deepEqual(problems, []);
    assert.match(pageText("getting-started.md"), /not shown in a customer account|platform and partner administrators/);
});
test("W6-T2 Agent Reviews is described as not in a customer account's menu where it is explained", () => {
    const about = sentencesOf(pageText("follow-ups.md")).filter((s) => /Agent Reviews/.test(s));
    assert.ok(about.length > 0, "follow-ups.md explains Agent Reviews");
    assert.match(about.join(" "), /platform and partner administrators|not shown in a customer account|not in a customer account/);
});
test("W6-T2 fixture", () => {
    assert.equal(adminAreaProblem("Depending on your plan and role you may also see Security Center or Agent Quality."), true);
    assert.equal(adminAreaProblem("Agent Quality is available on the Business plan."), true);
    assert.equal(adminAreaProblem("Account administrators can open Security Center."), true);
    assert.equal(adminAreaProblem("A customer account administrator sees **Agent Quality** in the sidebar."), true);
    assert.equal(adminAreaProblem("Administrators can open **Security Center**."), true); // "administrator" alone is not an honest answer
    assert.equal(adminAreaProblem("Security Center and Agent Quality are shown to platform and partner administrators only."), false);
    assert.equal(adminAreaProblem("**Security Center** and **Agent Quality** are not shown in a customer account."), false);
    assert.equal(adminAreaProblem("An account administrator cannot open Security Center."), false);
});

// ---------------------------------------------------------------------------------------------
// W6-T3: notes written for the team, not the reader.
const INTERNAL_NOTES =
    /intended for publication|written for the current|existing priority|partner-role dashboard|call-state|captured-facts|deployed release|reviewed procedures|technical team|Appendix A|Internal Notes/i;
test("W6-T3 no internal note to readers", () => assert.deepEqual(hits(INTERNAL_NOTES), []));
test("W6-T3 fixture", () => {
    for (const bad of [
        "This guide is intended for publication in the website documentation.",
        "The Ask AI homepage return and partner-role dashboard routing keep their existing priority.",
        "Verify that the latest call-state/captured-facts logic is active.",
        "Administrative screens depend on your role and the deployed release.",
        "Ask your Talk-Lee administrator/technical team to review telephony.",
    ]) assert.equal(INTERNAL_NOTES.test(bad), true, bad);
    assert.equal(INTERNAL_NOTES.test("Sign in again. Sessions end after inactivity."), false);
});

// ---------------------------------------------------------------------------------------------
// W6-T4: nothing about the developer pages, webhooks, tenant-wide keys or partner administration
// (not available in this release).
const UNAVAILABLE_AREAS = /\bwebhooks?\b|tenant-wide keys|developer hub|api-key workflow|\bpartners? (?:manage|administration)\b|white-label/i;
test("W6-T4 no Webhook, tenant-wide keys, developer-hub or partner wording", () => assert.deepEqual(hits(UNAVAILABLE_AREAS), []));
test("W6-T4 fixture", () => {
    for (const bad of ["| **Webhook** | An address Talk-Lee can send event notifications to. |", "for admins — tenant-wide keys and audit activity", "Open the Developer Hub.", "Partners manage several.", "plan a webhook, API-key or account-wide security workflow"])
        assert.equal(UNAVAILABLE_AREAS.test(bad), true, bad);
    assert.equal(UNAVAILABLE_AREAS.test("Do not include passwords, API keys or recordings in a support message."), false);
});

// ---------------------------------------------------------------------------------------------
// W6-T5: connectors.md has ONE status table, equal to src/lib/public-connector-status.ts.
type TableRow = { name: string; status: string; does: string; customerMust: string };
function connectorTables(text: string): TableRow[][] {
    const tables: TableRow[][] = [];
    let current: TableRow[] | null = null;
    for (const line of text.split(/\r?\n/)) {
        if (!line.trim().startsWith("|")) {
            current = null;
            continue;
        }
        const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
        if (cells.every((cell) => /^:?-+:?$/.test(cell))) continue;
        if (!current) {
            current = [];
            tables.push(current);
            continue; // the header row
        }
        const [name = "", status = "", does = "", customerMust = ""] = cells;
        current.push({ name: name.replace(/\*\*/g, ""), status, does, customerMust });
    }
    return tables;
}
function connectorTableProblems(text: string): string[] {
    const tables = connectorTables(text);
    if (tables.length !== 1) return [`expected exactly one table, found ${tables.length}`];
    const rows = tables[0];
    const problems: string[] = [];
    if (rows.length !== PUBLIC_CONNECTORS.length) problems.push(`the table has ${rows.length} rows, the data file has ${PUBLIC_CONNECTORS.length}`);
    PUBLIC_CONNECTORS.forEach((connector, index) => {
        const row = rows[index];
        if (!row) return;
        if (row.name !== connector.name) problems.push(`row ${index + 1}: name "${row.name}" is not "${connector.name}"`);
        if (row.status !== connector.status) problems.push(`${connector.name}: status "${row.status}" is not "${connector.status}"`);
        if (row.does !== connector.does) problems.push(`${connector.name}: "what it does" differs from the data file`);
        if (row.customerMust !== connector.customerMust) problems.push(`${connector.name}: "what you must set up" differs from the data file`);
    });
    return problems;
}
const connectorTableText = (rows: readonly { name: string; status: string; does: string; customerMust: string }[]) =>
    ["| Connector | Status | What it does | What you must set up |", "| --- | --- | --- | --- |", ...rows.map((r) => `| **${r.name}** | ${r.status} | ${r.does} | ${r.customerMust} |`)].join("\n");
test("W6-T5 the connectors page has one status table equal to the public connector data file", () =>
    assert.deepEqual(connectorTableProblems(pageText("connectors.md")), []));
test("W6-T5 fixture: a wrong status, a missing connector or a second table is flagged", () => {
    assert.deepEqual(connectorTableProblems(connectorTableText(PUBLIC_CONNECTORS)), []);
    const wrongStatus = PUBLIC_CONNECTORS.map((c) => (c.name === "HubSpot" ? { ...c, status: "Limited" } : c));
    assert.equal(connectorTableProblems(connectorTableText(wrongStatus)).length > 0, true);
    assert.equal(connectorTableProblems(connectorTableText(PUBLIC_CONNECTORS.slice(1))).length > 0, true);
    assert.equal(connectorTableProblems(`${connectorTableText(PUBLIC_CONNECTORS)}\n\ntext\n\n${connectorTableText(PUBLIC_CONNECTORS)}`).length > 0, true);
});

// ---------------------------------------------------------------------------------------------
// W6-T6: the contacts page documents what the dashboard has. Each label is read from the screen's
// source first, so the test also proves the control exists.
const CONTACT_CONTROLS: Array<{ label: string; source: string[]; why: string }> = [
    { label: "Do-Not-Call list", source: ["src", "app", "contacts", "page.tsx"], why: "the link above the Contacts list" },
    { label: "Add a number", source: ["src", "app", "contacts", "do-not-call", "page.tsx"], why: "single add on the Do-Not-Call page" },
    { label: "Import many", source: ["src", "app", "contacts", "do-not-call", "page.tsx"], why: "paste on the Do-Not-Call page" },
    { label: "Numbers, one per line", source: ["src", "app", "contacts", "do-not-call", "page.tsx"], why: "the paste box" },
    { label: "Add contact", source: ["src", "app", "contacts", "page.tsx"], why: "single add on Contacts" },
    { label: "Import Leads or Contacts into a campaign", source: ["src", "components", "connectors", "salesforce-settings.tsx"], why: "the Salesforce import" },
    { label: "Permission to call these contacts", source: ["src", "lib", "contact-permission.ts"], why: "the permission block" },
];
test("W6-T6 the contacts page documents the Do-Not-Call list, paste, single add, the permission block and the Salesforce import", () => {
    const text = pageText("contacts.md");
    for (const control of CONTACT_CONTROLS) {
        assert.ok(read(...control.source).includes(control.label), `"${control.label}" is not in ${control.source.join("/")} (${control.why})`);
        assert.ok(text.includes(control.label), `contacts.md does not document "${control.label}" (${control.why})`);
    }
});

// ---------------------------------------------------------------------------------------------
// W6-T7: the settings page documents every Settings tab (read from the screen) and sends no
// customer administrator to a page that answers 403.
// (settingsTabs is read at the top of this file, in the T1 section.)
// The pages are read WITH their bold names: the sentence is about the screen the name labels.
const ADMIN_SCREENS = /\bAudit (?:&|and) Access\b|\bAudit Logs\b|\bAudit activity\b|\bTenant-wide security\b/i;
// An honest sentence says the screen does not work or is not shown for a customer account.
const SAYS_PLATFORM_ONLY =
    /platform and partner administrators|not shown in a customer account|access-denied|answers? with an access|\b403\b|could not be loaded|\bneither works\b|\bdoes not work\b/i;
const adminScreenProblem = (s: string) => ADMIN_SCREENS.test(s) && !SAYS_PLATFORM_ONLY.test(s);
test("W6-T7 the settings page documents every Settings tab", () => {
    assert.deepEqual(settingsTabs, ["Profile", "Security", "Devices", "Telephony", "Recording"]);
    const text = pageText("settings-and-security.md");
    for (const tab of settingsTabs) assert.match(text, new RegExp(`\\*\\*${tab}\\*\\*|### ${tab}\\b|\\b${tab} tab`), `settings-and-security.md does not document the ${tab} tab`);
    assert.match(text, /### Devices/);
    assert.match(text, /### Recording/);
});
test("W6-T7 the audit screens never promise a customer account a working page", () => {
    const problems = pages.flatMap((page) => sentencesOf(page.text).filter(adminScreenProblem).map((s) => `${page.name}: "${s}"`));
    assert.deepEqual(problems, []);
});
test("W6-T7 fixture", () => {
    assert.equal(adminScreenProblem("Administrators also see Tenant-wide security: Audit activity and Audit and access."), true);
    assert.equal(adminScreenProblem("An account administrator can open **Audit & Access** from the sidebar."), true);
    assert.equal(adminScreenProblem("Open **Audit Logs** to see who changed what."), true);
    assert.equal(adminScreenProblem("**Audit activity** lists every change."), true);
    assert.equal(adminScreenProblem("Audit and access opens for platform and partner administrators; an account administrator sees an access-denied page."), false);
    assert.equal(adminScreenProblem("**Audit and access** opens a 403 page, and **Audit activity** says the audit log could not be loaded."), false);
    assert.equal(adminScreenProblem("**Audit & Access** is not shown in a customer account."), false);
});

// ---------------------------------------------------------------------------------------------
// W6-T8: minutes terms (owner decisions O8, O9) and one click to Billing.
// The heading "Buy AI minute top-ups" of billing.md keeps its wording on purpose: the anchor of that heading must not change.
const OLD_MINUTES_TERMS = /\bAI-minute\b|\bAI minute (?:top-ups?|balance)\b|platform minutes|telephone carrier service|separate telephone-service|\bPSTN\/SIP\b|telephone minutes|carrier minutes|billed separately/i;
test("W6-T8 the billing page uses the two minutes terms and says who sees Billing & Logs and that it opens Billing", () => {
    const text = pageText("billing.md");
    assert.match(text, /\bAI minutes\b/);
    assert.match(text, /Telephone \(PSTN \/ SIP\) usage is not included in AI minutes/);
    assert.match(text, /its cost depends on the phone line you connect/i);
    // Who sees Billing & Logs, and that it opens Billing (no particular count of clicks is required).
    assert.match(text, /\*\*Billing & Logs\*\* is shown to [^.\n]*(?:administrators?|billing)[^.\n]*/);
    assert.match(text, /\*\*Billing & Logs\*\*[^.\n]*\bopens? \*\*Billing\*\*|\bopens? \*\*Billing\*\*/);
    assert.doesNotMatch(text, /Select \*\*Billing & Logs\*\* and open/);
    const withoutHeadings = everything.map((source) => ({ name: source.name, text: source.text.replace(/^#{1,6}\s.*$/gm, "") }));
    assert.deepEqual(hits(OLD_MINUTES_TERMS, withoutHeadings), []);
});
test("W6-T8 fixture", () => {
    for (const bad of ["Review your AI-minute balance.", "Buy AI minute top-ups.", "Talk-Lee platform minutes consumed by AI conversation.", "Telephone carrier service and charges are separate.", "PSTN/SIP carrier usage is billed separately."])
        assert.equal(OLD_MINUTES_TERMS.test(bad), true, bad);
    assert.equal(OLD_MINUTES_TERMS.test("Telephone (PSTN / SIP) usage is not included in AI minutes."), false);
});

// ---------------------------------------------------------------------------------------------
// W6-T11 (a): every sentence about transfer names its direction and its condition (R9), and never
// promises that a person answers. "telephone route" is a phone line, not a call route, so it is not
// a trigger. A question does NOT excuse a transfer sentence.
// Triggers: the transfer words, and the plain ways of saying a caller is passed to a person. "steps in",
// "takes over" and a bare "route" are NOT triggers ("Complete the steps in the window."); a route is a
// trigger only when it is a call or a caller that is routed.
const TRANSFER =
    /transfer|hand(?:s|ed|ing)?[- ]?off|escalat|\brout(?:e|es|ed|ing)\s+(?:the |a |an |each |every |incoming |outgoing )?(?:calls?|callers?)\b|\bconnects?\s+(?:the |a )?callers?\s+to\b|\bput through\b|\bputs?\s+(?:a |the )?callers?\s+through\b/i;
const TRANSFER_SETUP =
    /\blists? transfer among (?:its|the) approved actions\b|\btransfer option is on\b|\bapproved list\b|\bset up for (?:the|your) campaign\b|\bwhen it is set up\b|\bwhen set up\b|\bdoes not guarantee\b/i;
// "plainly says that no transfer happens": the negation sits directly on the verb ("cannot transfer a call"),
// not somewhere before it ("If the agent cannot answer, it transfers the caller" does not count).
const TRANSFER_NONE =
    /\b(?:does not|do not|doesn't|cannot|can't|never)\s+(?:\w+\s+){0,2}?(?:transfer|hand off|hand over|route|escalate|connect)\w*\b|\bno (?:transfer|hand-?off)s?\b/i;
// A negation alone does not satisfy the rule: a set-up condition, or a plain "no transfer".
const TRANSFER_CONDITION = new RegExp(`${TRANSFER_SETUP.source}|${TRANSFER_NONE.source}`, "i");
const TRANSFER_DIRECTION = /\b(?:outgoing|outbound|incoming|inbound)\b|both ways/i;
// Direction is not needed for a sentence that says nothing is promised or that no transfer happens.
const TRANSFER_NEGATIVE = new RegExp(`${TRANSFER_NONE.source}|\\bdoes not guarantee\\b`, "i");
const TRANSFER_OVERPROMISE = /always (?:answers|picks up)|instantly|(?<!guarantee that )(?:a (?:person|human)|someone) will answer/i;
const transferProblem = (s: string) =>
    TRANSFER.test(unbold(s)) && (TRANSFER_OVERPROMISE.test(s) || !TRANSFER_CONDITION.test(s) || (!TRANSFER_DIRECTION.test(s) && !TRANSFER_NEGATIVE.test(s)));
test("W6-T11 every transfer sentence names its direction and its condition and promises no answer", () => {
    const problems = everything.flatMap((source) => sentencesOf(source.text).filter(transferProblem).map((s) => `${source.name}: "${s}"`));
    assert.deepEqual(problems, []);
});
test("W6-T11 fixture: transfer", () => {
    assert.equal(transferProblem("Transfer to a human if enabled."), true);
    assert.equal(transferProblem("Test human transfer if enabled."), true);
    assert.equal(transferProblem("On an outgoing campaign a transfer always picks up."), true);
    assert.equal(transferProblem("The agent connects the caller to your team when expertise is needed."), true);
    assert.equal(transferProblem("Qualified callers are put through to sales."), true);
    assert.equal(transferProblem("The agent puts a caller through to a manager."), true);
    assert.equal(transferProblem("A call can be handed off to your team."), true);
    // A negation that does not sit on the transfer verb is not an honest condition.
    assert.equal(transferProblem("If the agent cannot answer, it transfers the caller to your team."), true);
    assert.equal(transferProblem("On an outgoing campaign the agent cannot answer, so it transfers the call."), true);
    assert.equal(transferProblem("Without that set-up, an outgoing campaign cannot transfer a call."), false);
    assert.equal(transferProblem("Complete the steps in the window."), false);
    assert.equal(transferProblem("Your team takes over from there."), false);
    assert.equal(transferProblem("Choose the route you prefer."), false);
    assert.equal(transferProblem("On an outgoing campaign, a transfer works only when the campaign lists transfer among its approved actions and names the destination numbers."), false);
    assert.equal(transferProblem("On an incoming campaign, a transfer works only when the transfer option is on and the destination is on the approved list."), false);
    assert.equal(transferProblem("On an outgoing campaign, when the campaign lists transfer among its approved actions, someone will answer."), true);
    assert.equal(transferProblem("The system knows that a transfer was requested; it does not guarantee that someone answers."), false);
    assert.equal(transferProblem("A test does not guarantee that someone will answer a live transfer."), false);
    assert.equal(transferProblem("Confirm the telephone route is ready."), false);
    assert.equal(transferProblem("Select **Transfer to a human**."), false);
});

// W6-T11 (b): a callback is scheduled only on an OUTGOING campaign, and the AI agent makes the call
// back (R14). On an incoming call the agent only notes the request.
const CALLBACK_CLAIM =
    /(?:schedul\w*|book\w*|arrang\w*)[^.]{0,30}call[ -]?backs?|call[ -]?backs?[^.]{0,30}(?:schedul|book)|\bcalls? (?:the |that )?(?:person|caller|customer|contact|them)s? back\b|\bagrees? (?:on )?a time\b/i;
const CALLBACK_INCOMING = /\b(?:incoming|inbound)\b/i;
const CALLBACK_OUTGOING = /\b(?:outgoing|outbound)\b/i;
const CALLBACK_NEGATED = /\b(?:does not|do not|cannot|can't|only notes?)\b/i;
// The direction word must APPLY to the callback: a sentence that names the incoming side without saying that
// the agent only notes the request does not pass because "outbound" is also in it ("Unlike outbound
// campaigns, inbound campaigns also schedule callbacks."), and a claim with no direction at all does not pass.
const callbackProblem = (s: string) =>
    CALLBACK_CLAIM.test(s) && !CALLBACK_NEGATED.test(s) && (CALLBACK_INCOMING.test(s) || !CALLBACK_OUTGOING.test(s));
test("W6-T11 a scheduled callback is outgoing-only", () => {
    const problems = everything.flatMap((source) => sentencesOf(source.text).filter(callbackProblem).map((s) => `${source.name}: "${s}"`));
    assert.deepEqual(problems, []);
});
test("W6-T11 fixture: callbacks", () => {
    const f = callbackProblem;
    assert.equal(f("Unlike outbound campaigns, inbound campaigns also schedule callbacks."), true);
    assert.equal(f("On incoming calls the agent agrees a time and calls the person back."), true);
    assert.equal(f("On an outgoing campaign it can schedule a callback; on an incoming call it only notes a callback request."), false);
    assert.equal(f("Customer Support: inbound, resolve issues, handle escalations, book callbacks."), true);
    assert.equal(f("Configure approved actions such as a callback."), false); // no claim about scheduling
    assert.equal(f("Schedule callbacks when callers ask for one."), true);
    assert.equal(f("Test callback scheduling if enabled."), true);
    assert.equal(f("On an outgoing campaign the agent can schedule a callback and calls back at the agreed time."), false);
    assert.equal(f("On an incoming call the agent does not schedule a callback; it notes the request."), false);
});

// W6-T11 (c): the calling agent does not book appointments or meetings into a calendar (R8). Google
// Calendar and Google Drive are used by the assistant in the dashboard, not by calls.
const CALENDAR_CLAIM = /\b(?:book\w*|schedul\w*)\b[^.]{0,50}\b(?:appointments?|meetings?|interviews?|consultations?|showings?|tours?|calendar)\b|\b(?:appointment|interview|meeting) (?:booking|scheduling)\b|added to (?:google )?calendar/i;
// "is not" / "are not" elsewhere in the sentence does not excuse a booking claim ("Booking is not hard: the agent books meetings.").
const CALENDAR_OK = /\b(?:does not|do not|doesn't|cannot|can't|no calendar)\b|\bnot by calls\b/i;
test("W6-T11 the calling agent does not book into a calendar", () => assert.deepEqual(sentenceHits(CALENDAR_CLAIM, CALENDAR_OK), []));
test("W6-T11 fixture: calendar", () => {
    const f = (s: string) => flagged(CALENDAR_CLAIM, CALENDAR_OK, s);
    assert.equal(f("AI Receptionist: inbound, answer, route, book appointments, take messages."), true);
    assert.equal(f("Configure approved actions such as appointment booking."), true);
    assert.equal(f("Qualify leads and book consultations."), true);
    assert.equal(f("The agent does not book appointments or meetings into a calendar."), false);
    assert.equal(f("Booking is not hard: the agent books appointments for you."), true);
    assert.equal(f("Meetings are not a problem, the agent will schedule meetings in your calendar."), true);
    assert.equal(f("| Google Calendar | Limited | Lets the assistant read your upcoming events and book, change or cancel meetings. | Used by the assistant in the dashboard, not by calls. |"), false);
});

// W6-T11 (d): calls are logged to HubSpot or Salesforce on the Business plan (R16).
const LOGS_TO_CRM =
    /(?:log\w*|sync\w*|record\w*)[^.]{0,60}\b(?:hubspot|salesforce)\b|\b(?:hubspot|salesforce)\b[^.]{0,60}(?:log\w*|sync\w*)|\b(?:appears?|shows? up|shown|listed|posted|pushed|copied|saved)\b[^.]{0,40}\b(?:hubspot|salesforce)\b/i;
const CRM_OK = /Business plan|\b(?:does not|do not|cannot|can't)\b/i;
test("W6-T11 every sentence saying calls go to HubSpot or Salesforce says Business plan", () => assert.deepEqual(sentenceHits(LOGS_TO_CRM, CRM_OK), []));
test("W6-T11 fixture: CRM logging", () => {
    const f = (s: string) => flagged(LOGS_TO_CRM, CRM_OK, s);
    assert.equal(f("Calls are logged to HubSpot or Salesforce."), true);
    assert.equal(f("Every call appears in HubSpot."), true);
    assert.equal(f("On the Business plan, every call appears in HubSpot."), false);
    assert.equal(f("CRM connectors such as HubSpot and Salesforce are available."), false);
    assert.equal(f("On the Business plan, calls are logged to HubSpot or Salesforce once you connect one."), false);
});

// W6-T11 (e): no shared line; no other language than English; recordings are not deleted
// automatically after a number of days (the retention nightly job only counts, nothing is removed).
const SHARED_LINE = /shared (?:line|number|phone)/i;
const LANGUAGE_CLAIM = /multilingual|multi-language|any language|\b(?:multiple|many|several|other|different|supported|more than one) languages?\b|language options?|translat\w+/i;
const RECORDING_DELETED =
    /\brecordings?\b[^.]{0,80}\b(?:are|is|get|will be|being) (?:removed|deleted|purged|erased)\b|\b(?:removed|deleted|purged|erased) (?:automatically|after \d+)|older than[^.]{0,40}\b(?:removed|deleted|purged)|\brecordings?\b[^.]{0,60}\bexpir\w*/i;
const RECORDING_NOT_DELETED = /\bnot (?:removed|deleted|purged|erased) automatically\b|\bnot deleted automatically\b|\b(?:do not|don't|never) expire\b/i;
// An honest negative about a language or a shared line is allowed ("Speech in other languages is not recognised.").
const HONEST_NEGATIVE = /\b(?:not|no|never|cannot|can't|does not|do not|English only|only English|only in English)\b/i;
test("W6-T11 no shared line, no other language than English, no automatic recording deletion", () => {
    assert.deepEqual(sentenceHits(SHARED_LINE, HONEST_NEGATIVE), []);
    assert.deepEqual(sentenceHits(LANGUAGE_CLAIM, HONEST_NEGATIVE), []);
    assert.deepEqual(sentenceHits(RECORDING_DELETED, RECORDING_NOT_DELETED), []);
    assert.match(pageText("ai-options.md"), /Speech recognition is English only/);
});
test("W6-T11 fixture: line, language, retention", () => {
    assert.equal(SHARED_LINE.test("Use Talk-Lee's shared line to start."), true);
    assert.equal(LANGUAGE_CLAIM.test("Serve callers in multiple languages."), true);
    assert.equal(LANGUAGE_CLAIM.test("Speech recognition is English only in this release."), false);
    assert.equal(flagged(RECORDING_DELETED, RECORDING_NOT_DELETED, "Recordings older than the retention period are removed."), true);
    assert.equal(flagged(RECORDING_DELETED, RECORDING_NOT_DELETED, "Recordings are deleted after 90 days."), true);
    assert.equal(flagged(RECORDING_DELETED, RECORDING_NOT_DELETED, "Recordings are not deleted automatically in this release."), false);
    assert.equal(flagged(RECORDING_DELETED, RECORDING_NOT_DELETED, "Recordings expire after 90 days."), true);
    assert.equal(flagged(RECORDING_DELETED, RECORDING_NOT_DELETED, "Recordings do not expire in this release."), false);
    // The language and shared-line rules let an honest negative through, and still flag the claim.
    assert.equal(flagged(LANGUAGE_CLAIM, HONEST_NEGATIVE, "Speech in other languages is not recognised."), false);
    assert.equal(flagged(LANGUAGE_CLAIM, HONEST_NEGATIVE, "Callers can speak other languages."), true);
    assert.equal(flagged(SHARED_LINE, HONEST_NEGATIVE, "Talk-Lee does not offer a shared line."), false);
    assert.equal(flagged(SHARED_LINE, HONEST_NEGATIVE, "Start on Talk-Lee's shared line."), true);
});

// A phone number is a request that staff review, not an instant purchase.
const BUYS_NUMBER = /\b(?:buy|purchase|get|order)\b[^.]{0,20}\b(?:a|your|new) (?:phone )?number\b/i;
test("W6-T11 a phone number is a request that staff review", () => {
    assert.deepEqual(sentenceHits(BUYS_NUMBER, /\brequest\b|\bcannot\b|\bdoes not\b/i, pages), []);
    assert.match(pageText("settings-and-security.md"), /staff review/i);
    assert.match(pageText("settings-and-security.md"), /not instant/i);
});
test("W6-T11 fixture: phone number", () => {
    assert.equal(flagged(BUYS_NUMBER, /\brequest\b|\bcannot\b|\bdoes not\b/i, "Use Buy Now to buy a phone number in Telephony."), true);
    assert.equal(flagged(BUYS_NUMBER, /\brequest\b|\bcannot\b|\bdoes not\b/i, "Use Buy Now to request a phone number."), false);
});
// A number is never issued instantly, from Buy Now or anywhere else.
const INSTANT_NUMBER =
    /\b(?:phone )?numbers?\b[^.]{0,40}\b(?:issued|assigned|provisioned|activated|delivered|ready)\b[^.]{0,30}\b(?:instant\w*|immediate\w*|at once|straight away|right away|in seconds)\b|\binstant\w*\b[^.]{0,40}\b(?:phone )?numbers?\b/i;
const NOT_INSTANT = /\bnot instant\b|\bnot (?:issued|assigned|provisioned) instantly\b|\bnever instant/i;
test("W6-T11 a phone number is never issued instantly", () => assert.deepEqual(sentenceHits(INSTANT_NUMBER, NOT_INSTANT, pages), []));
test("W6-T11 fixture: instant number", () => {
    assert.equal(flagged(INSTANT_NUMBER, NOT_INSTANT, "Phone numbers are issued instantly from Buy Now."), true);
    assert.equal(flagged(INSTANT_NUMBER, NOT_INSTANT, "Get an instant number with Buy Now."), true);
    assert.equal(flagged(INSTANT_NUMBER, NOT_INSTANT, "A request is not instant: staff review each request."), false);
});

// ---------------------------------------------------------------------------------------------
// W6-R1: the recording default. A tenant with no saved policy IS recorded and the agent speaks the notice
// first (owner decision of 18 November 2026). No page may say recording is off, disabled or not active
// "until a policy is saved" or by default.
const RECORDING_OFF_BY_DEFAULT =
    /\b(?:recording|recordings|calls?)\b[^.]{0,60}\b(?:off|disabled|not active|inactive|not recorded|not made|not kept|switched off|turned off)\b[^.]{0,60}\b(?:until[^.]{0,40}\bpolic\w*|by default|unless[^.]{0,30}\bpolic\w*)/i;
const UNTIL_POLICY_SAVED = /\buntil (?:a |you save a |you set a |one is |your )?polic\w+(?: is| has been| are)? (?:saved|set)\b/i;
const recordingDefaultProblem = (s: string) => RECORDING_OFF_BY_DEFAULT.test(s) || (UNTIL_POLICY_SAVED.test(s) && /\brecord/i.test(s));
test("W6-R1 no page says recording is off until a policy is saved or by default", () => {
    const problems = everything.flatMap((source) => sentencesOf(source.text).filter(recordingDefaultProblem).map((s) => `${source.name}: "${s}"`));
    assert.deepEqual(problems, []);
    assert.match(pageText("settings-and-security.md"), /If no policy has been saved, calls are recorded and the agent speaks a recording notice first\./);
    assert.match(pageText("troubleshooting.md"), /with no saved policy, calls are recorded with a spoken notice/);
});
test("W6-R1 fixture: recording default", () => {
    assert.equal(recordingDefaultProblem("Recording is off until a policy is saved."), true);
    assert.equal(recordingDefaultProblem("Recording is disabled by default."), true);
    assert.equal(recordingDefaultProblem("Calls are not recorded until you save a policy."), true);
    assert.equal(recordingDefaultProblem("Recording stays inactive until a policy has been saved."), true);
    assert.equal(recordingDefaultProblem("If no policy has been saved, calls are recorded and the agent speaks a recording notice first."), false);
    assert.equal(recordingDefaultProblem("A saved **Do not record calls** means no call is recorded."), false);
});

// W6-R2: revoking a session signs that device out.
const REVOKE_STAYS_SIGNED_IN =
    /\brevok\w*\b[^.]{0,80}\b(?:does not|doesn't|do not|did not|will not|won't|not)\s+(?:sign|log)\w*\b[^.]{0,30}\bout\b|\brevok\w*\b[^.]{0,80}\b(?:stays?|remains?|still)\b[^.]{0,20}\b(?:signed|logged) in\b/i;
test("W6-R2 no page says a revoked session stays signed in", () => {
    const problems = pages.flatMap((page) => sentencesOf(page.text).filter((s) => REVOKE_STAYS_SIGNED_IN.test(s)).map((s) => `${page.name}: "${s}"`));
    assert.deepEqual(problems, []);
    assert.match(pageText("settings-and-security.md"), /Revoking an entry signs that device out/);
});
test("W6-R2 fixture: session revoke", () => {
    assert.equal(REVOKE_STAYS_SIGNED_IN.test("Revoking an entry does not sign that device out; changing your password does."), true);
    assert.equal(REVOKE_STAYS_SIGNED_IN.test("A revoked session stays signed in until it expires."), true);
    assert.equal(REVOKE_STAYS_SIGNED_IN.test("Revoking an entry signs that device out: its next request or token refresh is refused."), false);
});

// W6-R3: on the Do-Not-Call page, Remove, Add a number and Import many are for account administrators.
// The three controls are named in the DNC page source first, so the test also proves they exist.
test("W6-R3 the Do-Not-Call controls are described as administrator-only", () => {
    const source = read("src", "app", "contacts", "do-not-call", "page.tsx");
    for (const label of ["Remove", "Add a number", "Import many"]) assert.ok(source.includes(label), `"${label}" is not on the Do-Not-Call page`);
    const sentences = sentencesOf(pageText("contacts.md"));
    const about = sentences.filter((s) => /\*\*Remove\*\*/.test(s) && /\*\*Add a number\*\*/.test(s) && /\*\*Import many\*\*/.test(s));
    assert.equal(about.length > 0 && about.every((s) => ONLY_ADMIN_SEES.test(s)), true, "contacts.md has no sentence that gives all three controls to account administrators only");
    const loose = sentences.filter((s) => /\*\*(?:Remove|Add a number|Import many)\*\*/.test(s) && EVERYONE_CAN.test(s));
    assert.deepEqual(loose, []);
});
const ONLY_ADMIN_SEES = /\bonly (?:an )?account administrators?\b/i;
const EVERYONE_CAN = /\b(?:every|any|all)\s+(?:user|role|member)s?\b|\beveryone\b|\banyone\b|\bonly a role that may\b/i;
test("W6-R3 fixture: Do-Not-Call controls", () => {
    assert.equal(EVERYONE_CAN.test("Any user can use **Add a number**."), true);
    assert.equal(EVERYONE_CAN.test("Only a role that may change the list sees **Add a number** and **Import many**."), true);
    assert.equal(ONLY_ADMIN_SEES.test("Only an account administrator sees **Add a number**, **Import many** and **Remove**; other roles can view the list."), true);
    assert.equal(ONLY_ADMIN_SEES.test("Administrators see **Remove**."), false);
});

// W6-R4: top-ups. An account with no plan can still buy minutes, under the name Pay as you go.
test("W6-R4 the billing page says an account with no plan buys minutes under Pay as you go", () => {
    const sentences = sentencesOf(pageText("billing.md"));
    assert.ok(sentences.some((s) => /\bwithout a plan\b|\bno plan\b/i.test(s) && /\*\*Pay as you go\*\*/.test(s) && /no monthly fee/i.test(s)), "billing.md does not explain Pay as you go for an account with no plan");
});

// W6-R5: the Opt-out key does nothing in this release.
test("W6-R5 the settings page says the Opt-out key has no effect", () => {
    const text = pageText("settings-and-security.md");
    assert.match(text, /\*\*Opt-out key\*\* has no effect in this release\./);
    assert.match(text, /Callers cannot opt out of recording by pressing a key/);
});
