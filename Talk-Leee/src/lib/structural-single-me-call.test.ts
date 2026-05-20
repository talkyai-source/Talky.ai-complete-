/**
 * Phase 8 structural CI guard — single source for /auth/me.
 *
 * Phase 3 folded SuspensionStateProvider's parallel `/auth/me` query
 * into AuthContext. The dashboard mount used to fire two `/auth/me`
 * calls in parallel — a documented race condition where one would
 * land, the other would 401, and the second response could clobber
 * the first's state. After Phase 3 there should be exactly one caller
 * of `api.getMe()`: AuthContext (bootstrap + refreshUser).
 *
 * Exception: the OAuth-callback page (`app/auth/callback/page.tsx`)
 * calls getMe() once after `api.setToken(...)` to pick the
 * white-label vs. tenant landing destination. That call is BEFORE
 * AuthProvider has the new token in its state, so it can't yet rely
 * on `user`. Acceptable.
 *
 * If this test breaks, the new caller should either:
 *   (a) Read from `useAuth().user` instead — AuthContext already
 *       loaded it.
 *   (b) Call `useAuth().refreshUser()` to nudge AuthContext to
 *       re-fetch. The single fetch is then visible to all
 *       subscribers.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

const ALLOWLIST = new Set<string>([
    // Owner of the bootstrap + refreshUser /auth/me call.
    "lib/auth-context.tsx",
    // Defines getMe.
    "lib/api.ts",
    // OAuth-callback role-routing — runs before AuthProvider has the
    // token in state. See file-level comment in this test for why.
    "app/auth/callback/page.tsx",
]);

const CALL_PATTERN = /\bapi\s*\.\s*getMe\s*\(/;

// Strip line and block comments before matching so a reference in a
// comment (e.g. "// A transient 401 from `api.getMe()` …") doesn't fire
// a false positive. The replacements aren't a full JS parser — they're
// good enough for the call-site detection we need.
function stripComments(source: string): string {
    return source
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^[ \t]*\/\/.*$/gm, "")
        .replace(/[ \t]+\/\/.*$/gm, "");
}

function walk(dir: string): string[] {
    const out: string[] = [];
    for (const entry of readdirSync(dir)) {
        const abs = path.join(dir, entry);
        const stat = statSync(abs);
        if (stat.isDirectory()) {
            if (entry === "node_modules" || entry === ".next" || entry === "test-utils") continue;
            out.push(...walk(abs));
            continue;
        }
        if (!/\.(ts|tsx)$/.test(entry)) continue;
        if (entry.endsWith(".test.ts") || entry.endsWith(".test.tsx")) continue;
        out.push(abs);
    }
    return out;
}

test("api.getMe() callers are limited to AuthContext (+ OAuth callback)", () => {
    const violations: string[] = [];
    for (const file of walk(SRC_DIR)) {
        const rel = path.relative(SRC_DIR, file).replace(/\\/g, "/");
        if (ALLOWLIST.has(rel)) continue;
        const source = stripComments(readFileSync(file, "utf8"));
        if (!CALL_PATTERN.test(source)) continue;
        violations.push(
            `${rel}: calls api.getMe(). Consume useAuth().user instead, ` +
            `or call useAuth().refreshUser() if a fresh fetch is required.`,
        );
    }
    assert.equal(
        violations.length,
        0,
        `single-me-call violations:\n  - ${violations.join("\n  - ")}`,
    );
});
