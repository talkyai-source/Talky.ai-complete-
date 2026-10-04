import { afterEach, beforeEach, it } from "node:test";
import assert from "node:assert/strict";
import { POST } from "./route";
const originalFetch = globalThis.fetch;
const originalBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const originalOrigin = process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN;
const body = { request_id: "e060104a-883d-4f68-a2ca-73c091baef8a", name: " Visitor ", email: "visitor@example.com", company: "", message: "Hello" };
const receipt = { status: "accepted", receipt_id: body.request_id, accepted_at: "2026-10-04T00:00:00Z" };
function request(extraHeaders: Record<string, string> = {}, raw = JSON.stringify(body)) {
  return new Request("https://app.example/api/contact-enquiries", { method: "POST", headers: { Origin: "https://app.example", "Content-Type": "application/json", ...extraHeaders }, body: raw });
}
beforeEach(() => { process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN = "https://app.example"; });
afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL;
  else process.env.NEXT_PUBLIC_API_BASE_URL = originalBase;
  if (originalOrigin === undefined) delete process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN;
  else process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN = originalOrigin;
});

it("accepts configured public origin behind an internal Next URL without trusting forwarded host", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  let calls = 0;
  globalThis.fetch = async (_url, init) => {
    calls++; assert.equal(new Headers(init?.headers).get("origin"), "https://app.example");
    return Response.json(receipt, { status: 201 });
  };
  const incoming = (origin: string) => new Request("https://localhost:3000/api/contact-enquiries", {
    method: "POST", headers: { Origin: origin, "Content-Type": "application/json", "X-Forwarded-Host": "attacker.example", Host: "attacker.example" }, body: JSON.stringify(body),
  });
  assert.equal((await POST(incoming("https://app.example"))).status, 201);
  assert.equal((await POST(incoming("https://attacker.example"))).status, 403);
  assert.equal(calls, 1);
});

for (const publicOrigin of ["", "null", "https://user:pass@app.example", "https://app.example/contact", "https://app.example?x=1"]) {
  it(`fails closed for invalid canonical public origin ${publicOrigin || "missing"}`, async () => {
    process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN = publicOrigin;
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
    let calls = 0;
    globalThis.fetch = async () => { calls++; return Response.json(receipt); };
    assert.equal((await POST(request())).status, 503);
    assert.equal(calls, 0);
  });
}

it("forwards only normalized data and validated origin to the configured backend", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1/";
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "https://backend.example/api/v1/public/contact-enquiries");
    assert.deepEqual(init?.headers, { "Content-Type": "application/json", Origin: "https://app.example" });
    assert.equal(init?.credentials, "omit"); assert.equal(init?.redirect, "error");
    assert.deepEqual(JSON.parse(String(init?.body)), { ...body, name: "Visitor" });
    return Response.json({ ...receipt, private_detail: "must not leak" }, { status: 201 });
  };
  const result = await POST(request({ Authorization: "Bearer synthetic", Cookie: "synthetic=1", "X-Forwarded-For": "192.0.2.9", "X-Real-IP": "192.0.2.10" }));
  assert.equal(result.status, 201); assert.deepEqual(await result.json(), receipt);
  assert.equal(result.headers.get("cache-control"), "no-store");
});

it("canonicalizes an uppercase request UUID and recognizes its lowercase receipt", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  let sentId = "";
  globalThis.fetch = async (_url, init) => { sentId = JSON.parse(String(init?.body)).request_id; return Response.json(receipt); };
  const result = await POST(request({}, JSON.stringify({ ...body, request_id: body.request_id.toUpperCase() })));
  assert.equal(result.status, 200);
  assert.equal(sentId, body.request_id);
  assert.deepEqual(await result.json(), receipt);
});

for (const origin of ["", "null", "https://other.example"]) {
  it(`rejects untrusted Origin ${origin || "missing"} before forwarding`, async () => {
    globalThis.fetch = async () => { assert.fail("No upstream request expected"); };
    assert.equal((await POST(request({ Origin: origin }))).status, 403);
  });
}

for (const base of ["", "file:///api/v1", "https://app.example/api/v1", "https://u:p@backend.example/api/v1", "https://backend.example/api/v1?url=elsewhere", "https://backend.example/other"]) {
  it(`fails closed for missing or unsafe backend configuration ${base}`, async () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = base;
    let calls = 0;
    globalThis.fetch = async () => { calls++; return Response.json(receipt); };
    assert.equal((await POST(request())).status, 503);
    assert.equal(calls, 0);
  });
}

it("rejects oversized, invalid and extra-field requests without forwarding", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  globalThis.fetch = async () => { assert.fail("No upstream request expected"); };
  assert.equal((await POST(request({}, "x".repeat(12_001)))).status, 413);
  assert.equal((await POST(request({}, JSON.stringify({ ...body, message: "x".repeat(501) })))).status, 422);
  assert.equal((await POST(request({}, JSON.stringify({ ...body, url: "http://127.0.0.1" })))).status, 422);
  assert.equal((await POST(request({ "Content-Type": "text/plain" }))).status, 415);
});

it("rejects an internal self-proxy even when canonical public origin differs", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:3000/api/v1";
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json(receipt); };
  const incoming = new Request("https://localhost:3000/api/contact-enquiries", {
    method: "POST", headers: { Origin: "https://app.example", "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  assert.equal((await POST(incoming)).status, 503);
  assert.equal(calls, 0);
});

it("does not turn malformed upstream success or an exception into acceptance", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  globalThis.fetch = async () => Response.json({ status: "accepted" });
  assert.equal((await POST(request())).status, 502);
  globalThis.fetch = async () => { throw new Error("private upstream details"); };
  const result = await POST(request()); assert.equal(result.status, 503);
  assert.ok(!(await result.text()).includes("private upstream"));
});

it("preserves a safe throttling response without leaking upstream content", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example/api/v1";
  globalThis.fetch = async () => Response.json({ secret: "private" }, { status: 429, headers: { "Retry-After": "60" } });
  const result = await POST(request()); assert.equal(result.status, 429);
  assert.equal(result.headers.get("retry-after"), "60");
  assert.ok(!(await result.text()).includes("private"));
});
