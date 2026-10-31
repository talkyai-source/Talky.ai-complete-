import { test } from "node:test";
import assert from "node:assert/strict";
import { GET, POST } from "@/app/api/v1/[...path]/route";

for (const path of ["billing/plans", "billing/subscription", "billing/create-checkout-session", "billing/webhooks/stripe"]) {
  test(`local ${path} fails before auth, body/idempotency work, or webhook acknowledgment`, async () => {
    const post = path.includes("checkout") || path.includes("webhooks");
    const request = new Request(`https://frontend.example/api/v1/${path}`, { method: post ? "POST" : "GET", ...(post ? { body: JSON.stringify({ id: "evt_synthetic", type: "checkout.session.completed" }) } : {}) });
    let clones = 0, headerReads = 0;
    const originalGet = request.headers.get.bind(request.headers);
    request.headers.get = (name: string) => { headerReads++; return originalGet(name); };
    request.clone = () => { clones++; throw new Error("The local billing guard must precede idempotency claiming"); };
    const response = await (post ? POST : GET)(request, { params: Promise.resolve({ path: path.split("/") }) });
    assert.equal(response.status, 503);
    assert.equal(clones, 0); assert.equal(headerReads, 0); assert.equal(request.bodyUsed, false);
    assert.equal(response.headers.get("x-idempotency-key"), null);
    const payload = await response.json(); assert.equal(payload.error.code, "billing_backend_unavailable"); assert.equal(payload.received, undefined);
  });
}
