import assert from "node:assert/strict";
import { test } from "node:test";

type HeaderRule = { source: string; headers: { key: string; value: string }[] };
type Config = { headers?: () => Promise<HeaderRule[]> };

// The real next.config.ts (through withSentryConfig), so the rule set tested is the one that ships.
async function loadRules(): Promise<HeaderRule[]> {
    const mod = (await import("../../../next.config")) as { default: Config };
    assert.ok(mod.default.headers, "next.config.ts exports headers()");
    return mod.default.headers();
}

test("documentation screenshots are cacheable across visits, with a bounded staleness and no immutable", async () => {
    const rules = await loadRules();
    const rule = rules.find((r) => r.source === "/docs/:path*.webp");
    assert.ok(rule, "a rule for /docs/:path*.webp exists");
    const cc = rule.headers.find((h) => h.key === "Cache-Control")?.value ?? "";
    assert.equal(cc, "public, max-age=86400, stale-while-revalidate=604800");
    assert.ok(!/immutable/.test(cc), "filenames carry no content hash, so the images must stay revalidatable");
    assert.ok(/max-age=(\d+)/.test(cc) && Number(/max-age=(\d+)/.exec(cc)![1]) <= 86400, "a replaced screenshot must reach a returning reader within a day");
});

test("the documentation image rule matches only .webp under /docs/, never the HTML pages", async () => {
    const rules = await loadRules();
    const sources = rules.map((r) => r.source);
    assert.ok(sources.includes("/docs/:path*.webp"));
    assert.ok(!sources.some((s) => s === "/docs/:path*" || s === "/docs/:path*/" || s === "/:path*"), "no rule may catch /docs/<page>/ HTML");
});

test("the pre-existing header rules are unchanged", async () => {
    const rules = await loadRules();
    const byValue = Object.fromEntries(rules.filter((r) => r.source !== "/docs/:path*.webp").map((r) => [r.source, r.headers.map((h) => `${h.key}: ${h.value}`).join("; ")]));
    assert.deepEqual(byValue, {
        "/images/:path*": "Cache-Control: public, max-age=86400, stale-while-revalidate=604800",
        "/:path*.mp4": "Cache-Control: public, max-age=86400, stale-while-revalidate=604800",
        "/:path*.svg": "Cache-Control: public, max-age=604800, stale-while-revalidate=2592000",
        "/openapi.json": "Cache-Control: public, max-age=3600, stale-while-revalidate=86400",
        "/site.webmanifest": "Cache-Control: public, max-age=3600, stale-while-revalidate=86400",
        // Present outside development only (see next.config.ts); the test runner is not NODE_ENV=development.
        ...(process.env.NODE_ENV === "development" ? {} : { "/_next/static/:path*": "Cache-Control: public, max-age=31536000, immutable" }),
    });
});
