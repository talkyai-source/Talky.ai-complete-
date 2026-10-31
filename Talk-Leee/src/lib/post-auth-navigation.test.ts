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

test("every authenticated entry point ignores return URLs and opens a dashboard", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const app = path.join(here, "..", "app", "auth");
    const sources = [
        path.join(app, "login", "login-client.tsx"),
        path.join(app, "register", "register-client.tsx"),
        path.join(app, "callback", "page.tsx"),
    ].map((file) => readFileSync(file, "utf8"));

    for (const source of sources) {
        assert.match(source, /postAuthDashboard\s*\(/);
        assert.doesNotMatch(source, /\.get\(\s*["']next["']\s*\)/);
    }
});
