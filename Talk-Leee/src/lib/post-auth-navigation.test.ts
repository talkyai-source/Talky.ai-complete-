import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { postAuthDashboard } from "@/lib/post-auth-navigation";

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
    assert.match(source, /window\.location\.assign\s*\(\s*destination\s*\)/);
});

test("login uses the validated return-target helper while other entry points retain their defaults", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const app = path.join(here, "..", "app", "auth");
    const sources = [
        path.join(app, "login", "login-client.tsx"),
        path.join(app, "register", "register-client.tsx"),
        path.join(app, "callback", "page.tsx"),
    ].map((file) => readFileSync(file, "utf8"));

    for (const source of sources) {
        assert.match(source, /postAuthDashboard\s*\(/);
    }
    assert.match(sources[0]!, /\.get\(\s*["']next["']\s*\)/);
    assert.match(sources[0]!, /window\.location\.assign\s*\(\s*destination\s*\)/);
    assert.match(sources[0]!, /markFreshLogin\s*\(\s*\)/);
    for (const source of sources.slice(1)) {
        assert.doesNotMatch(source, /\.get\(\s*["']next["']\s*\)/);
    }
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
