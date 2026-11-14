import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

// QA F4-14: https://talkleeai.vercel.app answered 200 with the full site.
const oldNodeEnv = process.env.NODE_ENV;

beforeEach(() => Object.assign(process.env, { NODE_ENV: "production" }));
afterEach(() => {
  if (oldNodeEnv === undefined) Reflect.deleteProperty(process.env, "NODE_ENV");
  else Object.assign(process.env, { NODE_ENV: oldNodeEnv });
});

function request(origin: string, path: string) {
  return new NextRequest(`${origin}${path}`, { headers: { host: new URL(origin).host } });
}

test("the exact production alias redirects permanently to the canonical origin, path and query kept", async () => {
  const res = await proxy(request("https://talkleeai.vercel.app", "/pricing/?plan=pro&ref=a%20b"));
  assert.equal(res.status, 308);
  assert.equal(res.headers.get("location"), "https://talkleeai.com/pricing/?plan=pro&ref=a%20b");
});

test("the alias root redirects to the canonical root", async () => {
  const res = await proxy(request("https://talkleeai.vercel.app", "/"));
  assert.equal(res.status, 308);
  assert.equal(res.headers.get("location"), "https://talkleeai.com/");
});

test("preview deployment hosts are not redirected", async () => {
  for (const origin of [
    "https://talkleeai-git-my-branch-team.vercel.app",
    "https://talkleeai-abc123xyz-team.vercel.app",
    "https://evil-talkleeai.vercel.app",
  ]) {
    const res = await proxy(request(origin, "/pricing/"));
    assert.notEqual(res.status, 308, origin);
    assert.equal(res.headers.get("location"), null, origin);
  }
});

test("the canonical host and www are not redirected by the alias rule", async () => {
  const res = await proxy(request("https://talkleeai.com", "/pricing/"));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("location"), null);
});

test("localhost is not redirected", async () => {
  const res = await proxy(request("http://localhost:3000", "/pricing/"));
  assert.notEqual(res.status, 308);
  assert.equal(res.headers.get("location"), null);
});
