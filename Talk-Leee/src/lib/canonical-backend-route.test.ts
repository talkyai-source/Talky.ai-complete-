import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { GET, POST } from "@/app/api/v1/[...path]/route";

const originalEnv = process.env.NODE_ENV;
afterEach(() => {
  if (originalEnv === undefined) Reflect.deleteProperty(process.env, "NODE_ENV");
  else Object.assign(process.env, { NODE_ENV: originalEnv });
});

for (const [path, method] of [["partners", "GET"], ["tenants/synthetic/users", "POST"], ["email/send", "POST"], ["calls", "POST"]] as const) {
  test(`production local ${method} /${path} cannot use a historical legacy session`, async () => {
    Object.assign(process.env, { NODE_ENV: "production" });
    const request = new Request(`https://frontend.example/api/v1/${path}`, {
      method, headers: { authorization: "Bearer historical-local-session", cookie: "talklee_auth_token=historical-local-session" },
      ...(method === "POST" ? { body: '{"synthetic":true}' } : {}),
    });
    let reads = 0;
    request.headers.get = () => { reads++; throw new Error("Legacy auth admission must not run in production"); };
    request.clone = () => { throw new Error("No legacy idempotency claims"); };
    const response = await (method === "GET" ? GET : POST)(request, { params: Promise.resolve({ path: path.split("/") }) });
    assert.equal(response.status, 503);
    assert.equal(reads, 0);
    assert.equal(request.bodyUsed, false);
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(response.headers.get("x-idempotency-key"), null);
    assert.equal((await response.json()).error.code, "api_backend_unavailable");
  });
}

test("explicit development keeps the existing unauthenticated health path", async () => {
  Object.assign(process.env, { NODE_ENV: "development" });
  const response = await GET(new Request("http://localhost/api/v1/health"), { params: Promise.resolve({ path: ["health"] }) });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, "ok");
});
