/**
 * AH-Phase-B structural CI guard — exactly one HttpClient instance.
 *
 * Single-flight refresh dedup, the fresh-login grace window, and the
 * session-expired latch are all PER-INSTANCE state in the HttpClient.
 * Two instances each hold their own in-flight refresh promise, so a
 * pair of simultaneous 401s — one from api.ts, one from a sibling
 * module — fires /auth/refresh twice in parallel. The second response
 * can clobber the first's token write, and the latch may bounce the
 * user to /auth/login from the second 401 while the first is mid-flight.
 *
 * Every browser-side wrapper now delegates to the single shared
 * instance via `sharedHttpClient()` exported from `lib/api.ts`. The
 * only legitimate `createHttpClient` calls are:
 *   - `lib/api.ts`     — the single instance.
 *   - `app/api/voices/route.ts` — server-side Next.js route handler
 *     (Vercel function, no AuthContext, per-request lifecycle).
 *
 * If this test breaks, route the new caller through `sharedHttpClient()`
 * (preferred) or add an entry to the allowlist with a comment
 * justifying why the new caller is a server-side / per-request module.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

const ALLOWLIST = new Set<string>([
    // Defines createHttpClient itself.
    "lib/http-client.ts",
    // Owns the single shared instance (sharedHttpClient).
    "lib/api.ts",
    // Server-side Next.js API route. Per-request lifecycle; runs on
    // Vercel functions where AuthContext doesn't exist. Acceptable.
    "app/api/voices/route.ts",
]);

const CALL_PATTERN = /\bcreateHttpClient\s*\(/;

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

test("only api.ts (and the server-side route) call createHttpClient", () => {
    const violations: string[] = [];
    for (const file of walk(SRC_DIR)) {
        const rel = path.relative(SRC_DIR, file).replace(/\\/g, "/");
        if (ALLOWLIST.has(rel)) continue;
        const source = stripComments(readFileSync(file, "utf8"));
        if (!CALL_PATTERN.test(source)) continue;
        violations.push(
            `${rel}: calls createHttpClient(). Delegate to sharedHttpClient() ` +
            `imported from "@/lib/api" — single instance means one single-flight ` +
            `refresh state for the whole browser session.`,
        );
    }
    assert.equal(
        violations.length,
        0,
        `multiple-HttpClient violations:\n  - ${violations.join("\n  - ")}`,
    );
});
