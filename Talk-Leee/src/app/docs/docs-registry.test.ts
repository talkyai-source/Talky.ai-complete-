import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { docsPages } from "./_content/registry";
import sitemap from "./../sitemap";

const CONTENT_DIR = path.join(process.cwd(), "src", "app", "docs", "_content");

test("every documentation page has a content file", () => {
    for (const page of docsPages) {
        const file = path.join(CONTENT_DIR, page.file);
        assert.ok(fs.existsSync(file), `${page.slug}: missing ${page.file}`);
        assert.ok(fs.readFileSync(file, "utf8").trim().length > 0, `${page.slug}: ${page.file} is empty`);
    }
});

test("documentation slugs are unique and url-safe", () => {
    const slugs = docsPages.map((page) => page.slug);
    assert.equal(new Set(slugs).size, slugs.length, "duplicate slug");
    for (const slug of slugs) assert.match(slug, /^[a-z0-9]+(?:-[a-z0-9]+)*$/, `bad slug: ${slug}`);
});

test("every documentation page has a title and a description", () => {
    for (const page of docsPages) {
        assert.ok(page.title.trim().length > 0, `${page.slug}: empty title`);
        assert.ok(page.description.trim().length > 0, `${page.slug}: empty description`);
    }
});

test("the sitemap contains the index and every documentation route, trailing-slashed", () => {
    const urls = new Set(sitemap().map((entry) => entry.url));
    assert.ok(urls.has("https://talkleeai.com/docs/"), "missing /docs/");
    for (const page of docsPages) {
        assert.ok(urls.has(`https://talkleeai.com/docs/${page.slug}/`), `missing /docs/${page.slug}/`);
    }
});

test("no published content carries the guide's internal appendix", () => {
    for (const page of docsPages) {
        const text = fs.readFileSync(path.join(CONTENT_DIR, page.file), "utf8");
        assert.ok(!/Appendix A/.test(text), `${page.file} mentions Appendix A`);
        assert.ok(!/Internal Notes/.test(text), `${page.file} mentions Internal Notes`);
    }
});

// The backend's support index parses these slugs (backend/app/domain/services/support/articles.py).
// A title or a description may change; a slug or a file name may not.
test("the documentation slugs and file names are the ones the backend reads", () => {
    assert.deepEqual(
        docsPages.map((page) => [page.slug, page.file]),
        [
            ["getting-started", "getting-started.md"],
            ["outbound-campaigns", "outbound-campaigns.md"],
            ["contacts", "contacts.md"],
            ["inbound", "inbound.md"],
            ["calls-and-recordings", "calls-and-recordings.md"],
            ["follow-ups", "follow-ups.md"],
            ["connectors", "connectors.md"],
            ["ai-options", "ai-options.md"],
            ["billing", "billing.md"],
            ["settings-and-security", "settings-and-security.md"],
            ["workflows", "workflows.md"],
            ["troubleshooting", "troubleshooting.md"],
            ["glossary", "glossary.md"],
        ],
    );
});
