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
