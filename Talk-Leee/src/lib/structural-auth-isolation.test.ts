/**
 * Phase 8 structural CI guard — authentication-token isolation.
 *
 * The universal-auth-state plan's invariant #1: AuthContext is the
 * single writer/reader of the canonical localStorage token key.
 * `src/lib/auth-token.ts` exists to hold the get/set helpers; nobody
 * else should import `getBrowserAuthToken` / `setBrowserAuthToken` /
 * `consumeLegacyAuthCookie` directly.
 *
 * If this test breaks, someone reintroduced a localStorage-snapshot
 * pattern — those are exactly what Phase 5 spent two evenings ripping
 * out. Either:
 *   (a) Refactor the offending file to consume `useAccessToken()`
 *       (or `api.request()` for non-React contexts).
 *   (b) Add a one-line entry to the allowlist below with a comment
 *       explaining *why* this file legitimately needs raw token
 *       access — the bar should be high.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

// Files allowed to import token helpers / read the canonical key.
// Keep this list short. Every entry needs a comment with the reason.
const ALLOWLIST = new Set<string>([
    // Owner of the canonical localStorage key + the cookie-migration helper.
    "lib/auth-token.ts",
    // The single in-app caller; rest of the codebase consumes via
    // useAccessToken() / useAuth() / api.request().
    "lib/auth-context.tsx",
    // Bridges the shared HttpClient to the persisted token via a
    // callback `getToken: () => getBrowserAuthToken()`. The callback
    // shape means the token is read at REQUEST time, not snapshotted,
    // so this stays reactive. Acceptable until backend-api.ts is
    // collapsed into api.ts (future structural cleanup, not Phase 8).
    "lib/backend-api.ts",
    // Singleton API client. `api.setToken(token)` (called by login,
    // register, OAuth-callback) writes through to setBrowserAuthToken
    // and resets the http-client's session-expired latch. The shared
    // client is the second writer-of-record alongside AuthContext —
    // AuthContext delegates to it for the token-commit step.
    "lib/api.ts",
]);

const FORBIDDEN_IMPORTS = [
    /import\s*\{[^}]*\bgetBrowserAuthToken\b[^}]*\}\s*from\s*["']@\/lib\/auth-token["']/,
    /import\s*\{[^}]*\bsetBrowserAuthToken\b[^}]*\}\s*from\s*["']@\/lib\/auth-token["']/,
    /import\s*\{[^}]*\bconsumeLegacyAuthCookie\b[^}]*\}\s*from\s*["']@\/lib\/auth-token["']/,
];

const FORBIDDEN_DIRECT_KEY = /localStorage\s*\.\s*(get|set|remove)Item\s*\(\s*["']talklee\.auth\.token["']/;

// Strip line and block comments before matching so a documentation
// reference in a comment (e.g. "// previous version called
// getBrowserAuthToken() …") doesn't fire a false positive.
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

test("only auth-context (and allowlisted bridges) read the canonical token", () => {
    const violations: string[] = [];
    for (const file of walk(SRC_DIR)) {
        const rel = path.relative(SRC_DIR, file).replace(/\\/g, "/");
        if (ALLOWLIST.has(rel)) continue;
        const source = stripComments(readFileSync(file, "utf8"));
        for (const pattern of FORBIDDEN_IMPORTS) {
            if (pattern.test(source)) {
                violations.push(
                    `${rel}: imports a token helper from @/lib/auth-token. ` +
                    `Use useAccessToken() in components or api.request() in non-React code.`,
                );
                break;
            }
        }
        if (FORBIDDEN_DIRECT_KEY.test(source)) {
            violations.push(
                `${rel}: directly accesses localStorage["talklee.auth.token"]. ` +
                `Only auth-context.tsx may touch the canonical key.`,
            );
        }
    }
    assert.equal(
        violations.length,
        0,
        `auth-token isolation violations:\n  - ${violations.join("\n  - ")}`,
    );
});
