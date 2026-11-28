import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { postAuthDashboard, safeInternalPath } from "@/lib/post-auth-navigation";

test("every customer role lands on the customer dashboard", () => {
    for (const role of [undefined, null, "tenant_admin", "owner", "user", "readonly"]) {
        assert.equal(postAuthDashboard(role), "/dashboard");
    }
});

test("white-label admins land on their dashboard", () => {
    assert.equal(postAuthDashboard("white_label_admin"), "/white-label/dashboard");
});

test("registration hydrates auth state before performing a hard dashboard navigation", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(
        path.join(here, "..", "app", "auth", "register", "register-client.tsx"),
        "utf8",
    );

    assert.match(source, /applyLoginResult\s*\(\s*\{/);
    assert.match(source, /access_token:\s*response\.access_token/);
    assert.match(source, /markFreshLogin\s*\(\s*\)/);
    // The hard navigation is injected (onNavigate) with window.location.assign as its default.
    assert.match(source, /onNavigate\(destination\)/);
    assert.match(source, /const navigate = \(url: string\) => window\.location\.assign\(url\);/);
});

test("login uses the validated return-target helper while other entry points retain their defaults", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const app = path.join(here, "..", "app", "auth");
    // QA F4-09: the callback page no longer signs anyone in, so it is not a
    // post-auth entry point; it must not apply a login at all.
    const sources = [
        path.join(app, "login", "login-client.tsx"),
        path.join(app, "register", "register-client.tsx"),
    ].map((file) => readFileSync(file, "utf8"));
    const callback = readFileSync(path.join(app, "callback", "page.tsx"), "utf8");
    assert.doesNotMatch(callback, /applyLoginResult|setToken|getMe\s*\(/);

    for (const source of sources) {
        assert.match(source, /postAuthDashboard\s*\(/);
    }
    assert.match(sources[0]!, /\.get\(\s*["']next["']\s*\)/);
    assert.match(sources[0]!, /window\.location\.assign\s*\(\s*destination\s*\)/);
    assert.match(sources[0]!, /markFreshLogin\s*\(\s*\)/);
});

// Sign-up follows a safe `next` too (W2). The rule that keeps that safe, stated so each part can fail:
const registerDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app", "auth", "register");
const codeOnly = (source: string) =>
    source
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/.*$/gm, "")
        .replace(/^\s*import\b.*$/gm, "");

test("sign-up (a): register-next.ts is the only file of the sign-up folder that reads `next` from the address", () => {
    const readers = readdirSync(registerDir)
        .filter((name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))
        .filter((name) => /\.get\(\s*["']next["']\s*\)/.test(codeOnly(readFileSync(path.join(registerDir, name), "utf8"))));
    assert.deepEqual(readers, ["register-next.ts"]);
});

test("sign-up (b)+(c): register-client.tsx uses `next` only as an argument of postAuthDashboard, destinationAfterSignup and signInHref, never of a navigation", () => {
    const code = codeOnly(readFileSync(path.join(registerDir, "register-client.tsx"), "utf8"));
    assert.doesNotMatch(code, /(?:router\.(?:push|replace)|location\.(?:assign|replace)|onNavigate|navigate)\(\s*next\b/);
    assert.doesNotMatch(code, /location(?:\.href)?\s*=\s*next\b/);
    const leftover = code
        .replace(/const next = nextFromAddress\(useSearchParams\(\)\);/g, "")
        .replace(/postAuthDashboard\(\s*[\w.]+\s*,\s*next\s*\)/g, "")
        .replace(/destinationAfterSignup\(\s*[\w.]+\s*,\s*next\s*,\s*\w+\s*\)/g, "")
        .replace(/signInHref\(next\)/g, "")
        .replace(/createdHere, next, router/g, "");
    assert.doesNotMatch(leftover, /\bnext\b/, "every other use of `next` in register-client.tsx must go through the shared check");
    // And the destination really is the helper's result (register-next.ts calls postAuthDashboard).
    assert.match(readFileSync(path.join(registerDir, "register-next.ts"), "utf8"), /postAuthDashboard\(role, next\)/);
});

for (const destination of ["/billing/", "/calls?search=Alice%20Smith#recent", "/campaigns/11111111-1111-4111-8111-111111111111"]) {
    test(`a safe original deep link is preserved: ${destination}`, () => {
        assert.equal(postAuthDashboard("tenant_admin", destination), destination);
        assert.equal(postAuthDashboard("readonly", destination), destination);
    });
}

for (const destination of [
    "https://evil.example/path", "http://localhost/billing", "javascript:alert(1)",
    "//evil.example/path", "///evil.example", "billing", "", " /billing",
    "/\\evil.example", "\\\\evil.example", "/billing\\..\\auth\\login", "/\n/evil.example",
    "/\t/evil.example", "/billing\u0000", "/billing\u007f", "/billing\u0085",
    "/%2f/evil.example", "/%5cevil.example", "/%252f%252fevil.example", "/%250a/evil.example",
    "/%0d%0a/evil.example", "/%", "/auth/login", "/auth/register?next=/billing",
    "/AUTH/login", "/auth", "/%61uth/login", "/calls/../auth/login", "/x/%2e%2e/auth/callback",
    "/billing?unsafe=%5cevil.example", "/billing?value=%0a", "/" + "x".repeat(4096),
]) {
    test(`unsafe or auth-loop return target falls back: ${JSON.stringify(destination)}`, () => {
        assert.equal(postAuthDashboard("tenant_admin", destination), "/dashboard");
    });
}

test("missing return targets preserve every caller's established role destination", () => {
    for (const value of [undefined, null]) {
        assert.equal(postAuthDashboard("tenant_admin", value), "/dashboard");
        assert.equal(postAuthDashboard("white_label_admin", value), "/white-label/dashboard");
    }
});

test("white-label routing keeps precedence over customer return targets", () => {
    for (const value of ["/billing", "/calls", "//evil.example"]) {
        assert.equal(postAuthDashboard("white_label_admin", value), "/white-label/dashboard");
    }
});

for (const value of ["/campaigns/new", "/meetings?tab=1", "/billing/plans/?interval=year", "/billing/", "/dashboard", "/campaigns/123"]) {
    test(`safeInternalPath keeps a same-site path: ${value}`, () => {
        assert.equal(safeInternalPath(value), value);
    });
}

for (const value of [
    "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)",
    "/%2F%2Fevil.example", "", null, undefined,
    "/billing/..//evil.example", "/billing/%2e%2e/%2e%2e//evil.example",
    "/api/v1/auth/logout",
    "/API/v1/auth/logout",
    "/api",
    "/_next/static/x",
    "/billing/../_next/static/x",
]) {
    test(`safeInternalPath rejects: ${JSON.stringify(value)}`, () => {
        assert.equal(safeInternalPath(value), "");
    });
}
