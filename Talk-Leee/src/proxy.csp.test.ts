import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

// QA F4-12: the Cloudflare Turnstile widget is one script and one iframe, both
// served from challenges.cloudflare.com. The page policy must allow those two
// and nothing else.
const TURNSTILE_ORIGIN = "https://challenges.cloudflare.com";
const oldNodeEnv = process.env.NODE_ENV;

beforeEach(() => Object.assign(process.env, { NODE_ENV: "production" }));
afterEach(() => {
  if (oldNodeEnv === undefined) Reflect.deleteProperty(process.env, "NODE_ENV");
  else Object.assign(process.env, { NODE_ENV: oldNodeEnv });
});

async function policy(path: string) {
  const res = await proxy(new NextRequest(`https://frontend.example${path}`));
  const csp = res.headers.get("content-security-policy");
  assert.ok(csp, `${path} carries a Content-Security-Policy`);
  return new Map(csp.split(";").map((directive) => {
    const [name, ...sources] = directive.trim().split(/\s+/);
    return [name, sources] as const;
  }));
}

test("the sign-in, sign-up and forgot-password pages may load the Turnstile script and frame", async () => {
  for (const path of ["/auth/login/", "/auth/register/", "/auth/forgot-password/"]) {
    const csp = await policy(path);
    assert.ok(csp.get("script-src")?.includes(TURNSTILE_ORIGIN), `${path} script-src must allow ${TURNSTILE_ORIGIN}`);
    assert.ok(csp.get("frame-src")?.includes(TURNSTILE_ORIGIN), `${path} frame-src must allow ${TURNSTILE_ORIGIN}`);
  }
});

test("Turnstile is added to script-src and frame-src only, and what was allowed before still is", async () => {
  const csp = await policy("/auth/login/");
  assert.ok(csp.get("script-src")?.includes("'self'"));
  assert.ok(csp.get("frame-src")?.includes("https://calendar.google.com"));
  for (const [name, sources] of csp) {
    if (name === "script-src" || name === "frame-src") continue;
    assert.ok(!sources.includes(TURNSTILE_ORIGIN), `${name} must not list ${TURNSTILE_ORIGIN}`);
  }
  assert.deepEqual(csp.get("object-src"), ["'none'"]);
  assert.deepEqual(csp.get("frame-ancestors"), ["'none'"]);
});
