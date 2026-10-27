import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { POST } from "./route";

const keys = ["NEXT_PUBLIC_API_BASE_URL", "CONTACT_ENQUIRY_PUBLIC_ORIGIN", "CONTACT_ENQUIRY_API_BASE_URL"] as const;
const original = Object.fromEntries(keys.map(key => [key, process.env[key]]));
const originalFetch = globalThis.fetch;
const body = { request_id: "a8d5f48d-61b3-4b5a-abd5-e956fecc3a1f", name: "Owned visitor",
  email: "visitor@example.invalid", company: "", message: "Synthetic intake request" };
const receipt = { status: "accepted", receipt_id: body.request_id, accepted_at: "2026-10-21T00:00:00Z" };
const request = () => new Request("http://127.0.0.1:58198/api/contact-enquiries", {
  method: "POST", headers: { Origin: "http://127.0.0.1:58197", "Content-Type": "application/json",
    Cookie: "must-not-forward=synthetic", Authorization: "Bearer must-not-forward" }, body: JSON.stringify(body),
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  for (const key of keys) {
    if (original[key] === undefined) delete process.env[key];
    else process.env[key] = original[key];
  }
});

test("same-origin browser API uses the explicitly configured server contact upstream", async () => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:58197/api/v1";
  process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN = "http://127.0.0.1:58197";
  process.env.CONTACT_ENQUIRY_API_BASE_URL = "http://127.0.0.1:58202/api/v1";
  let calls = 0;
  globalThis.fetch = async (url, init) => {
    calls++;
    assert.equal(String(url), "http://127.0.0.1:58202/api/v1/public/contact-enquiries");
    assert.deepEqual(init?.headers, { "Content-Type": "application/json", Origin: "http://127.0.0.1:58197" });
    assert.equal(init?.credentials, "omit"); assert.equal(init?.redirect, "error");
    return Response.json(receipt, { status: 201 });
  };
  const result = await POST(request());
  assert.equal(result.status, 201);
  assert.deepEqual(await result.json(), receipt);
  assert.equal(calls, 1);
});

for (const upstream of ["", "http://127.0.0.1:58197/api/v1", "http://127.0.0.1:58198/api/v1",
  "http://user:password@backend.invalid/api/v1", "http://backend.invalid/api/v1?next=elsewhere",
  "file:///api/v1", "http://backend.invalid/another-path"]) {
  test(`an explicitly invalid server upstream is not replaced by a usable public fallback: ${upstream || 'empty'}`, async () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://fallback.example.invalid/api/v1";
    process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN = "http://127.0.0.1:58197";
    process.env.CONTACT_ENQUIRY_API_BASE_URL = upstream;
    let calls = 0;
    globalThis.fetch = async () => { calls++; return Response.json(receipt); };
    const result = await POST(request());
    assert.equal(result.status, 503);
    assert.equal((await result.json()).outcome, "not_sent");
    assert.equal(calls, 0);
  });
}

test("the prior separate-origin backend remains supported when no server override is supplied", async () => {
  delete process.env.CONTACT_ENQUIRY_API_BASE_URL;
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://backend.example.invalid/api/v1";
  process.env.CONTACT_ENQUIRY_PUBLIC_ORIGIN = "http://127.0.0.1:58197";
  globalThis.fetch = async url => {
    assert.equal(String(url), "https://backend.example.invalid/api/v1/public/contact-enquiries");
    return Response.json(receipt, { status: 200 });
  };
  assert.equal((await POST(request())).status, 200);
});
