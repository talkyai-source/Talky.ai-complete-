import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import Module from "node:module";
import { test } from "node:test";
import type { Metadata } from "next";
import robots from "@/app/robots";
import sitemap from "@/app/sitemap";
import { docsPages } from "@/app/docs/_content/registry";
import { PUBLIC_ROUTES } from "@/lib/public-routes";
import { BRAND_TITLE_SUFFIX, SITE_URL, pageTitle, sharedMetadata } from "@/lib/site-metadata";

// The home page loads a font through next/font/local, which only the Next
// compiler provides; the metadata does not depend on it.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/font/local") return { __esModule: true, default: () => ({ className: "", variable: "", style: {} }) };
    return originalLoad.call(this, id, ...args);
};

// W8: every public page has its own title ending in one brand suffix, and its
// own description. The metadata is read from the page modules themselves.

// layout.tsx imports the stylesheet, which the test runner cannot load, so the
// root description is read from its source.
const rootDescription = /description: "([^"]+)"/.exec(
    fs.readFileSync(path.join(process.cwd(), "src/app/layout.tsx"), "utf8"),
)?.[1];
const rootMetadata = { description: rootDescription, metadataBase: sharedMetadata.metadataBase };

// The docs pages (/docs*) keep their own title form. A new page under that
// folder is NOT covered by the title and description checks below.
const OTHER_FORM = (route: string) => route === "/docs" || route.startsWith("/docs/");

const checked = PUBLIC_ROUTES.filter((route) => !OTHER_FORM(route));

async function metadataOf(route: string): Promise<Metadata> {
    const file = route === "/" ? "@/app/page" : `@/app${route}/page`;
    const mod = (await import(file)) as { metadata?: Metadata };
    assert.ok(mod.metadata, `${route} exports metadata`);
    return mod.metadata;
}

test("the brand suffix is one constant and pageTitle builds 'Name | suffix'", () => {
    assert.equal(BRAND_TITLE_SUFFIX, "Talk-Lee AI");
    assert.equal(pageTitle("Products"), "Products | Talk-Lee AI");
});

test("T1: every checked public page has a unique title ending in the brand suffix (home may begin with it)", async () => {
    const titles = new Map<string, string>();
    for (const route of checked) {
        const title = (await metadataOf(route)).title;
        assert.equal(typeof title, "string", `${route} has a plain string title`);
        const text = title as string;
        const ok = route === "/" ? text.startsWith(BRAND_TITLE_SUFFIX) : text.endsWith(` | ${BRAND_TITLE_SUFFIX}`);
        assert.ok(ok, `${route}: "${text}" does not carry the brand suffix`);
        assert.ok(!titles.has(text), `${route} and ${titles.get(text)} share the title "${text}"`);
        titles.set(text, route);
    }
    assert.ok(checked.length >= 24);
});

test("T2: every checked public page has its own description, never the root one", async () => {
    const root = rootMetadata.description;
    assert.ok(root);
    const seen = new Map<string, string>();
    for (const route of checked) {
        const description = (await metadataOf(route)).description;
        assert.ok(description && description.length > 20, `${route} has a description`);
        assert.notEqual(description, root, `${route} repeats the root description`);
        assert.ok(!seen.has(description), `${route} and ${seen.get(description)} share a description`);
        seen.set(description, route);
    }
});

test("T2b: the descriptions of the pages claim nothing untrue", async () => {
    const banned = /free trial|white[- ]label|24\/7|at scale|human-like|\bSMS\b|\bchat\b|appointment/i;
    for (const route of checked) {
        const description = (await metadataOf(route)).description ?? "";
        assert.doesNotMatch(description, banned, `${route}: ${description}`);
    }
});

test("T3: the canonical base is the one site origin", () => {
    assert.equal(SITE_URL, "https://talkleeai.com");
    assert.equal((rootMetadata.metadataBase as URL).origin, "https://talkleeai.com");
});

test("T4: the sitemap lists exactly the public routes, none signed-in, and stamps none with a date", () => {
    const entries = sitemap();
    const routes = entries.map((e) => {
        const p = new URL(e.url).pathname;
        return p === "/" ? "/" : p.replace(/\/$/, "");
    });
    assert.deepEqual(routes, [...PUBLIC_ROUTES]);
    for (const route of routes) {
        assert.doesNotMatch(route, /^\/(dashboard|calls|campaigns|billing|admin|settings|team|recordings|403)(\/|$)/);
    }
    // The only date available is the build time, which would claim that every
    // page changed on every deploy.
    assert.ok(entries.every((e) => e.lastModified === undefined), "a sitemap entry carries a date");
});

const SIGNED_IN_AND_UTILITY = [
    "/recordings", "/team", "/notifications", "/support", "/assistant", "/email",
    "/meetings", "/reminders", "/auth/callback",
];

test("T5: robots disallows the signed-in and utility paths and no public route", () => {
    const rules = robots().rules as { disallow: string[] };
    const disallow = rules.disallow;
    for (const p of SIGNED_IN_AND_UTILITY) assert.ok(disallow.includes(p), `${p} is not disallowed`);
    // A robots rule is a prefix of the address: "/team" would also block "/teamwork/".
    for (const route of PUBLIC_ROUTES) {
        if (route === "/") continue;
        for (const d of disallow) {
            assert.ok(!`${route}/`.startsWith(d), `${route} is public but robots disallows ${d}`);
        }
    }
});

test("T5b: the two noindex pages are not disallowed, so a crawler can read the noindex", () => {
    const disallow = (robots().rules as { disallow: string[] }).disallow;
    // A crawler that may not fetch a page never sees its noindex, and a
    // disallow line would name the error-check page in a public file.
    for (const p of ["/403", "/sentry-example-page"]) {
        assert.ok(!disallow.some((d) => `${p}/`.startsWith(d)), `${p} is disallowed`);
    }
});

test("T6: /403 and /sentry-example-page are kept out of search results", async () => {
    const forbidden = (await import("@/app/403/page")) as { metadata?: Metadata };
    assert.equal((forbidden.metadata?.robots as { index?: boolean } | undefined)?.index, false);
    const layout = (await import("@/app/sentry-example-page/layout")) as { metadata?: Metadata };
    assert.equal((layout.metadata?.robots as { index?: boolean } | undefined)?.index, false);
    assert.equal((layout.metadata?.robots as { follow?: boolean } | undefined)?.follow, false);
});

test("T6b: /403 and /sentry-example-page each have a description of their own", async () => {
    const a = (await import("@/app/403/page")) as { metadata?: Metadata };
    const b = (await import("@/app/sentry-example-page/layout")) as { metadata?: Metadata };
    assert.ok(a.metadata?.description && b.metadata?.description);
    assert.notEqual(a.metadata.description, rootMetadata.description);
    assert.notEqual(b.metadata.description, rootMetadata.description);
    assert.notEqual(a.metadata.description, b.metadata.description);
});

type RedirectRule = { source: string; destination: string; permanent: boolean };

async function redirectRules(): Promise<RedirectRule[]> {
    const mod = (await import("../../next.config")) as { default: { redirects?: () => Promise<RedirectRule[]> } };
    assert.ok(mod.default.redirects);
    return mod.default.redirects();
}

test("T7: the three old addresses redirect once, with and without the slash, to a public page", async () => {
    const rules = await redirectRules();
    // The two docs pages are withheld only until they are enabled again, so
    // their redirects are temporary; the /ai-voices page was removed for good.
    const expected = [
        { from: "/docs/support", to: "/docs/", permanent: false },
        { from: "/docs/partners", to: "/docs/", permanent: false },
        { from: "/ai-voices", to: "/ai-voice-agent/", permanent: true },
    ];
    for (const { from, to, permanent } of expected) {
        for (const source of [from, `${from}/`]) {
            const rule = rules.find((r) => r.source === source);
            assert.ok(rule, `no redirect for ${source}`);
            // The destination carries the trailing slash, or the proxy adds a second redirect.
            assert.equal(rule.destination, to);
            assert.equal(rule.permanent, permanent);
        }
        assert.ok((PUBLIC_ROUTES as readonly string[]).includes(to.replace(/\/$/, "")), `${to} is not a public route`);
    }
});

test("T7b: no redirect hides a page that is published", async () => {
    // A redirect runs before the page route, so an entry left behind would
    // silently hide a page that is published later.
    const published = new Set<string>([...PUBLIC_ROUTES, ...docsPages.map((page) => `/docs/${page.slug}`)]);
    for (const rule of await redirectRules()) {
        const source = rule.source === "/" ? "/" : rule.source.replace(/\/$/, "");
        assert.ok(!published.has(source), `${rule.source} redirects away from a published page`);
    }
});

test("T10 (guard): src/app/head.tsx stays absent, so no page renders a second description tag", () => {
    assert.equal(fs.existsSync(path.join(process.cwd(), "src/app/head.tsx")), false);
});
