/**
 * Phase 8 structural CI guard — no bare fetch() to /api/v1.
 *
 * The universal-auth-state plan's invariant #3: every authenticated
 * request goes through the shared `api` HttpClient (lib/api.ts +
 * lib/http-client.ts), which is the only thing that knows the access
 * token, the refresh path, the single-flight refresh dedup, the
 * fresh-login grace window, and the unified session-expired latch.
 * Bare `fetch()` calls to the backend bypass all of that.
 *
 * Heuristic: a file imports `apiBaseUrl` from `@/lib/env` AND has at
 * least one `fetch(` call. That combination is the signature of a bare
 * api fetch — the legitimate consumers of `apiBaseUrl` are the
 * http-client (which uses it to construct request URLs) and a handful
 * of allowlisted special-case sites listed below.
 *
 * If this test breaks, route the fetch through `api.request()` instead.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

const ALLOWLIST = new Set<string>([
    // Owns the actual fetch() that powers every other request.
    "lib/http-client.ts",
    // Builds the URLs that http-client consumes. Doesn't fetch directly
    // here, but does import apiBaseUrl for the error metadata.
    "lib/api.ts",
    // Defines apiBaseUrl itself.
    "lib/env.ts",
    // Resolves a WebSocket base URL from apiBaseUrl. WS connections are
    // not fetch and are exempt by design.
    "components/assistant/floating-assistant.tsx",
    "components/ui/voice-agent-popup.tsx",
    // FormData multipart upload — http-client.request() assumes JSON
    // body. Routing this through api.request() would require body
    // detection + binary blob response handling. Stays as a bare fetch
    // with the http-client's getToken callback for now.
    "lib/extended-api.ts",
    // Server-side helper module that constructs absolute URLs for
    // Next.js API route handlers — not browser fetches.
    "lib/api-server.ts",
    // Edge middleware that probes the backend for the user context.
    // Runs on Vercel's edge runtime where the shared client isn't loaded.
    "middleware.ts",
    // Server-side helper (Next.js route handlers / RSC). Uses
    // next/headers + next/cookies and forwards the caller's cookies to
    // the backend. The shared HttpClient is a browser module; the
    // server-side path needs raw fetch.
    "lib/server-auth.ts",
]);

// Strip line and block comments before matching so a `fetch(` in a
// migration note (e.g. "// previous version used fetch(`${apiBaseUrl()}…`)")
// doesn't fire a false positive.
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

const IMPORTS_API_BASE_URL = /import\s*\{[^}]*\bapiBaseUrl\b[^}]*\}\s*from\s*["']@\/lib\/env["']/;
const HAS_FETCH_CALL = /\bfetch\s*\(/;

test("no bare fetch() to /api/v1 outside the shared HttpClient", () => {
    const violations: string[] = [];
    for (const file of walk(SRC_DIR)) {
        const rel = path.relative(SRC_DIR, file).replace(/\\/g, "/");
        if (ALLOWLIST.has(rel)) continue;
        const source = stripComments(readFileSync(file, "utf8"));
        if (!IMPORTS_API_BASE_URL.test(source)) continue;
        if (!HAS_FETCH_CALL.test(source)) continue;
        // The combination of importing apiBaseUrl AND calling fetch()
        // strongly suggests a bare backend fetch.
        violations.push(
            `${rel}: imports apiBaseUrl and calls fetch(). ` +
            `Use api.request({ path, method, body }) instead — that routes through ` +
            `the shared HttpClient with refresh/grace/latch participation.`,
        );
    }
    assert.equal(
        violations.length,
        0,
        `bare-fetch violations:\n  - ${violations.join("\n  - ")}`,
    );
});
