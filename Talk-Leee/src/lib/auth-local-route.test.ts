import assert from "node:assert/strict";
import { test } from "node:test";
import { DELETE, GET, PATCH, POST } from "@/app/api/v1/[...path]/route";

for (const [path, handler, method] of [
  ["auth", GET, "GET"],
  ["auth/me", GET, "GET"],
  ["me", GET, "GET"],
  ["auth/register", POST, "POST"],
  ["auth/login", POST, "POST"],
  ["auth/refresh", POST, "POST"],
  ["auth/mfa/verify", POST, "POST"],
  ["auth/passkeys/synthetic", DELETE, "DELETE"],
  ["auth/profile", PATCH, "PATCH"],
] as const) {
  test(`local ${method} /${path} cannot authenticate or mutate legacy identity`, async () => {
    const request = new Request(`https://frontend.example/api/v1/${path}`, {
      method,
      ...(method === "GET" ? {} : { body: '{"synthetic":true}' }),
    });
    let headerReads = 0;
    request.headers.get = () => { headerReads++; throw new Error("Auth must be unavailable before credentials are inspected"); };
    request.clone = () => { throw new Error("No local idempotency/body processing is authorized"); };
    const response = await handler(request, { params: Promise.resolve({ path: path.split("/") }) });
    assert.equal(response.status, 503);
    assert.equal(headerReads, 0);
    assert.equal(request.bodyUsed, false);
    assert.equal(response.headers.get("set-cookie"), null);
    assert.equal(response.headers.get("x-idempotency-key"), null);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), {
      error: { code: "auth_backend_unavailable", message: "Authentication requires the configured backend service." },
    });
  });
}
