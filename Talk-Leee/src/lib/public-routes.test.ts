import assert from "node:assert/strict";
import { test } from "node:test";
import sitemap from "@/app/sitemap";
import { PUBLIC_ROUTES, isPublicRoute } from "./public-routes";

test("the sitemap publishes exactly the shared public route list, in order", () => {
    const fromSitemap = sitemap().map((entry) => {
        const path = new URL(entry.url).pathname;
        return path === "/" ? "/" : path.replace(/\/$/, "");
    });
    assert.deepEqual(fromSitemap, [...PUBLIC_ROUTES]);
});

test("the list holds 36 distinct routes without trailing slashes", () => {
    assert.equal(PUBLIC_ROUTES.length, 36);
    assert.equal(new Set(PUBLIC_ROUTES).size, 36);
    for (const route of PUBLIC_ROUTES) {
        if (route !== "/") assert.doesNotMatch(route, /\/$/, `${route} carries a trailing slash`);
    }
});

test("isPublicRoute matches exactly, never by prefix", () => {
    assert.equal(isPublicRoute("/contact"), true);
    assert.equal(isPublicRoute("/contacts"), false);
    assert.equal(isPublicRoute("/docs"), true);
    assert.equal(isPublicRoute("/docs/getting-started"), true);
    assert.equal(isPublicRoute("/docs/not-a-page"), false);
    assert.equal(isPublicRoute("/ai-voices"), true);
    assert.equal(isPublicRoute("/ai-options"), false);
    assert.equal(isPublicRoute("/pricing/"), true, "the canonical trailing-slash form usePathname() reports");
    assert.equal(isPublicRoute("/contacts/"), false);
    assert.equal(isPublicRoute("/pricing//"), false);
    assert.equal(isPublicRoute("/"), true);
    assert.equal(isPublicRoute("/dashboard"), false);
    assert.equal(isPublicRoute("/auth/register"), false);
    assert.equal(isPublicRoute("/auth/login"), false);
    assert.equal(isPublicRoute(""), false);
});
