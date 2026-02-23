import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

function readProjectFile(relPath: string) {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const projectRoot = path.join(here, "..", "..");
    return readFileSync(path.join(projectRoot, relPath), "utf8");
}

test("middleware protects dashboard routes without dev token bypass", () => {
    const middleware = readProjectFile("middleware.ts");
    assert.match(middleware, /token\s*!==\s*"dev-token"/);
    assert.doesNotMatch(middleware, /value:\s*"dev-token"/);
    assert.doesNotMatch(middleware, /TALKLEE_REQUIRE_AUTH/);
    assert.match(middleware, /url\.pathname\s*=\s*"\/auth\/login"/);
});

test("home navbar does not auto-seed auth token", () => {
    const navbar = readProjectFile("src/components/home/navbar.tsx");
    assert.doesNotMatch(navbar, /setBrowserAuthToken\(/);
    assert.doesNotMatch(navbar, /dev-token/);
});

test("api auth client does not return development auth stubs", () => {
    const apiFile = readProjectFile("src/lib/api.ts");
    assert.doesNotMatch(apiFile, /Development mode: login bypassed/);
    assert.doesNotMatch(apiFile, /isDevAuthStubEnabled/);
});

test("auth context clears invalid tokens instead of accepting unknown users", () => {
    const authContext = readProjectFile("src/lib/auth-context.tsx");
    assert.doesNotMatch(authContext, /id:\s*"unknown"/);
    assert.match(authContext, /api\.clearToken\(\)/);
});

test("sidebar logout uses auth logout and forces sign-in route", () => {
    const sidebar = readProjectFile("src/components/layout/sidebar.tsx");
    assert.match(sidebar, /await logout\(\)/);
    assert.match(sidebar, /router\.replace\("\/auth\/login\?logged_out=1"\)/);
    assert.doesNotMatch(sidebar, /window\.location\.href\s*=\s*"\/"\s*;/);
});
