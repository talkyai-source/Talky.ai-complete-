import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

const originalFetch = globalThis.fetch;
const oldNodeEnv = process.env.NODE_ENV;
const oldBase = process.env.NEXT_PUBLIC_API_BASE_URL;
beforeEach(() => Object.assign(process.env, { NODE_ENV: "production", NEXT_PUBLIC_API_BASE_URL: "https://backend.example/api/v1/" }));
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (oldNodeEnv === undefined) Reflect.deleteProperty(process.env, "NODE_ENV");
  else Object.assign(process.env, { NODE_ENV: oldNodeEnv });
  if (oldBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = oldBase;
});
const request = () => new NextRequest("https://frontend.example/white-label/dashboard/", {
  headers: { cookie: "talky_at=synthetic-access; talky_sid=synthetic-session" },
});

test("proxy role lookup is canonical, uncached, and observes a later revocation", async () => {
  const calls: Array<{ url: string; init?: RequestInit & { next?: unknown } }> = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return calls.length === 1 ? Response.json({ role: "white_label_admin" }) : new Response(null, { status: 401 });
  };
  assert.equal((await proxy(request())).headers.get("x-middleware-next"), "1");
  assert.equal((await proxy(request())).headers.get("location"), "https://frontend.example/403/");
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.url, "https://backend.example/api/v1/auth/me");
    assert.equal(call.init?.cache, "no-store");
    assert.equal(call.init?.next, undefined);
    assert.equal(new Headers(call.init?.headers).get("cookie"), request().headers.get("cookie"));
  }
});

test("proxy does not retry an alternate /me authority or the unconfigured local catchall", async () => {
  const urls: string[] = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return String(url).endsWith("/auth/me") ? new Response(null, { status: 404 }) : Response.json({ role: "white_label_admin" });
  };
  assert.equal((await proxy(request())).headers.get("location"), "https://frontend.example/403/");
  assert.deepEqual(urls, ["https://backend.example/api/v1/auth/me"]);
  delete process.env.NEXT_PUBLIC_API_BASE_URL;
  assert.equal((await proxy(request())).headers.get("location"), "https://frontend.example/403/");
  assert.equal(urls.length, 1);
});
