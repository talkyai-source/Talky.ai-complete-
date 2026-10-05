// Use Next's real request cookies API, with isolated synthetic request stores.
import "next/dist/server/node-environment-baseline";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { NextRequest } from "next/server";
import { createRequestStoreForAPI } from "next/dist/server/async-storage/request-store";
import { workAsyncStorage, type WorkStore } from "next/dist/server/app-render/work-async-storage.external";
import { workUnitAsyncStorage } from "next/dist/server/app-render/work-unit-async-storage.external";
import { getServerMe } from "./server-auth";

const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const principal = { id: "synthetic-user", email: "person@example.test", role: "tenant_admin" };
const requests: Array<{ url: string; init?: RequestInit }> = [];

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1/";
  requests.length = 0;
  globalThis.fetch = async (url, init) => { requests.push({ url: String(url), init }); return Response.json(principal); };
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = originalBase;
});

function read(cookie: string) {
  const req = new NextRequest("https://frontend.example/dashboard/", { headers: { cookie } });
  const requestStore = createRequestStoreForAPI(req, req.nextUrl, { tags: [], expirationsByCacheKind: new Map() }, undefined, undefined, undefined);
  requestStore.phase = "render";
  return workAsyncStorage.run({ route: "/dashboard", isStaticGeneration: false } as WorkStore,
    () => workUnitAsyncStorage.run(requestStore, getServerMe));
}

for (const cookie of ["talky_at=synthetic-access", "talky_sid=synthetic-session", "talky_at=synthetic-access; talky_sid=synthetic-session"]) {
  test(`SSR verifies canonical ${cookie.split("=")[0]} cookies with the current backend`, async () => {
    assert.deepEqual(await read(`${cookie}; tracking=unrelated; talklee_auth_token=stale-mirror`), principal);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "https://backend.example/api/v1/auth/me");
    assert.equal(new Headers(requests[0].init?.headers).get("cookie"), cookie);
    assert.equal(requests[0].init?.cache, "no-store");
  });
}

test("SSR does not authenticate from a legacy mirror alone or without configured backend", async () => {
  assert.equal(await read("talklee_auth_token=legacy"), null);
  delete process.env.NEXT_PUBLIC_API_BASE_URL;
  assert.equal(await read("talky_at=synthetic-access"), null);
  assert.equal(requests.length, 0);
});

for (const status of [401, 403, 404, 503]) {
  test(`SSR ${status} cannot fall back to another identity authority`, async () => {
    globalThis.fetch = async (url, init) => {
      requests.push({ url: String(url), init });
      return String(url).endsWith("/auth/me") ? new Response(null, { status }) : Response.json(principal);
    };
    assert.equal(await read("talky_at=synthetic-access; talklee_auth_token=legacy"), null);
    assert.deepEqual(requests.map(({ url }) => url), ["https://backend.example/api/v1/auth/me"]);
  });
}

test("SSR successive request scopes see revocation, with no persistent principal cache", async () => {
  let calls = 0;
  globalThis.fetch = async (url, init) => {
    requests.push({ url: String(url), init });
    return calls++ === 0 ? Response.json(principal) : new Response(null, { status: 401 });
  };
  assert.deepEqual(await read("talky_at=synthetic-access"), principal);
  assert.equal(await read("talky_at=synthetic-access"), null);
  assert.equal(requests.length, 2);
  assert.ok(requests.every(({ init }) => init?.cache === "no-store"));
});

test("SSR failed or malformed principal responses do not grant a principal", async () => {
  for (const data of [null, [], { role: "" }, { role: "tenant_admin" }]) {
    globalThis.fetch = async () => Response.json(data);
    assert.equal(await read("talky_at=synthetic-access"), null);
  }
  globalThis.fetch = async () => { throw new Error("synthetic connection loss"); };
  assert.equal(await read("talky_at=synthetic-access"), null);
});
