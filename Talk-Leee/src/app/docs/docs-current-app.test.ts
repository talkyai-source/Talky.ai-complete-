import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { docsPages } from "./_content/registry";
import { CAMPAIGN_PREPARING_LABEL, CAMPAIGN_STATUS_FILTER_LABELS, CAMPAIGN_STOPPING_LABEL } from "../../lib/campaign-status";

// FX8 (plan P9): the published documentation follows the current application.

const CONTENT_DIR = path.join(process.cwd(), "src", "app", "docs", "_content");
const IMAGE_DIR = path.join(process.cwd(), "public", "docs");

const published = docsPages.map((page) => ({ file: page.file, text: fs.readFileSync(path.join(CONTENT_DIR, page.file), "utf8") }));

/** Every /docs/<name> image a markdown text shows. */
function imagesOf(text: string): string[] {
    return Array.from(text.matchAll(/!\[[^\]]*\]\(\/docs\/([^)\s]+)\)/g), (match) => match[1]);
}

test("every image a published page uses exists in public/docs", () => {
    let seen = 0;
    for (const page of published) {
        for (const name of imagesOf(page.text)) {
            seen += 1;
            assert.equal(fs.existsSync(path.join(IMAGE_DIR, name)), true, `${page.file} shows /docs/${name}, which is not in public/docs`);
        }
    }
    assert.ok(seen > 0, "the published pages show images");
});

test("every file in public/docs is used by a published page", () => {
    const used = new Set(published.flatMap((page) => imagesOf(page.text)));
    const files = fs.readdirSync(IMAGE_DIR);
    assert.ok(files.length > 0, "public/docs holds the images");
    for (const name of files) {
        assert.equal(used.has(name), true, `public/docs/${name} is not shown by any published page`);
    }
});

// FX5 (P8): these six routes answer with the not-found page (src/app/hidden-pages.fx5.test.tsx).
// /assistant/actions is served and is not in this list.
const HIDDEN_ROUTE_LINK = /\]\(\/(?:email|reminders|meetings|assistant(?:\/(?:meetings|reminders))?)\/?(?:[#?][^)]*)?\)/;
// The hidden pages' names. "Email" is also a field label, so it counts only as a bold name, a heading or an "area".
const HIDDEN_PAGE_NAMES = [
    /\bAssistant\b/,
    /\bMeetings\b/,
    /\bReminders\b/,
    /\*\*Email\*\*/,
    /^#{1,6}\s+Email\s*$/m,
    /\bEmail area\b/,
];

test("no published page links to a hidden page or names it as available", () => {
    for (const page of published) {
        assert.doesNotMatch(page.text, HIDDEN_ROUTE_LINK, `${page.file} links to a page that is not served`);
        for (const name of HIDDEN_PAGE_NAMES) {
            assert.doesNotMatch(page.text, name, `${page.file} names a page that is not served`);
        }
    }
});

test("the campaign status names in the docs are the application's", () => {
    const text = published.find((page) => page.file === "outbound-campaigns.md")?.text ?? "";
    const section = /### Campaign statuses\n([\s\S]*?)\n#{2,3} /.exec(text)?.[1] ?? "";
    const names = Array.from(section.matchAll(/^- \*\*([^*]+)\*\*/gm), (match) => match[1]);
    assert.ok(names.length > 0, "the Campaign statuses list is present");
    const appNames = [...CAMPAIGN_STATUS_FILTER_LABELS, CAMPAIGN_PREPARING_LABEL, CAMPAIGN_STOPPING_LABEL];
    for (const name of names) {
        assert.equal(appNames.includes(name), true, `"${name}" is not a campaign status name of the application`);
    }
    assert.equal(names.includes("Running"), true, "a campaign that is calling is named Running");
});

// Sign-off (plan P9): the registry descriptions follow the same rule as the pages.
// "assistant actions" stays allowed: /assistant/actions is served.
const HIDDEN_NAMES_IN_DESCRIPTION = [...HIDDEN_PAGE_NAMES, /\bmeetings\b/i, /\breminders\b/i, /\bemail\b/i];

test("no registry description names a hidden page", () => {
    for (const page of docsPages) {
        for (const name of HIDDEN_NAMES_IN_DESCRIPTION) {
            assert.doesNotMatch(page.description, name, `the ${page.slug} description names a page that is not served`);
        }
    }
});

test("no published page says real outbound calls are always agent-first", () => {
    for (const page of published) {
        assert.doesNotMatch(page.text, /always\s+agent-first/i, `${page.file} says live calls are always agent-first`);
    }
});

// The owner-approved sentences (18 November), word for word. `line: true` = the whole line is the sentence.
const APPROVED: Array<{ id: string; file: string; text: string; line: boolean }> = [
    { id: "A", file: "getting-started.md", line: true, text: "- **Team** — see who is in your account; administrators can invite teammates and change their roles" },
    { id: "B", file: "getting-started.md", line: true, text: "Figures and charts on the Dashboard use your account's reporting timezone. The zone is shown under the page title as **Reporting: {zone}**, and each chart states the zone it uses as **Timezone: {zone}**. An account administrator can change it under **Settings → Profile → Timezone**." },
    { id: "C1", file: "outbound-campaigns.md", line: true, text: "- **Stopped** — calling has ended; the campaign can be started again" },
    { id: "C2", file: "outbound-campaigns.md", line: true, text: "- **Preparing** — the campaign is getting ready to call after **Start** or **Resume**" },
    { id: "C3", file: "outbound-campaigns.md", line: true, text: "- **Stopping** — a stop request is in progress" },
    { id: "D", file: "outbound-campaigns.md", line: false, text: " asks who opens the conversation. This choice applies to the test only. For live calls, the choice is made in the **Start campaign** window." },
    { id: "E", file: "outbound-campaigns.md", line: true, text: "Tick **Call anytime (ignore these hours)** to allow calls outside the window above. Calling outside these hours depends on platform settings and is not guaranteed." },
    { id: "F1", file: "outbound-campaigns.md", line: true, text: "When you start a single campaign, the window also offers:" },
    { id: "F2", file: "outbound-campaigns.md", line: true, text: "- **Calls at a time (batch size)** — how many calls dial at once; a new call starts only when an earlier one finishes" },
    { id: "F3", file: "outbound-campaigns.md", line: true, text: "- **Wait between calls** — the minimum gap before the next call is dialed; set it to 0 for no wait" },
    { id: "F4", file: "outbound-campaigns.md", line: true, text: "While a campaign is running, select **Pause** to stop calling temporarily, then **Resume** to continue; **Resume** opens the same window. Select **Stop** to end calling; a stopped campaign can be started again." },
    { id: "G", file: "outbound-campaigns.md", line: true, text: "   - **Resume Selected** — opens the **Start campaign** window for the selected campaigns; any readiness warning is shown there" },
    { id: "H1", file: "follow-ups.md", line: true, text: "## Actions" },
    { id: "H2", file: "follow-ups.md", line: true, text: "Actions taken through the AI assistant are saved, with their outcomes, on the **Actions** page (/assistant/actions). When the assistant shows **View actions** beside an action that is still in progress, select it to open that page." },
    { id: "I", file: "follow-ups.md", line: true, text: "Notifications include new qualified leads, failed calls and low remaining minutes. To choose which kinds of notification show a pop-up, and their priority, use **Notification Preferences** on the **Settings** page." },
    { id: "J", file: "settings-and-security.md", line: true, text: "- Timezone — the zone used for dates on the Dashboard and in Analytics; it does not change campaign calling hours. Only an account administrator can change it." },
    { id: "K1", file: "outbound-campaigns.md", line: true, text: "![The campaign page header with the Duplicate button](/docs/outbound-campaigns-13-duplicate.webp)" },
    { id: "K2", file: "troubleshooting.md", line: true, text: "![A campaign page showing a telephone readiness warning beside the campaign controls](/docs/outbound-campaigns-04-readiness-warning.webp)" },
];

for (const approved of APPROVED) {
    test(`owner-approved sentence ${approved.id} is in ${approved.file}, word for word`, () => {
        const text = published.find((page) => page.file === approved.file)?.text ?? "";
        const found = approved.line ? text.split(/\r?\n/).includes(approved.text) : text.includes(approved.text);
        assert.equal(found, true, `${approved.file} does not hold the approved sentence ${approved.id} as written`);
    });
}

test("owner-approved sentence H is the first section of follow-ups.md", () => {
    const text = published.find((page) => page.file === "follow-ups.md")?.text ?? "";
    assert.equal(text.startsWith("## Actions\n") || text.startsWith("## Actions\r\n"), true);
});

test("owner-approved sentence L is the follow-ups description, word for word", () => {
    const description = docsPages.find((page) => page.slug === "follow-ups")?.description;
    assert.equal(description, "Notifications, Agent Reviews and assistant actions that follow up on your calls.");
});
