import { afterEach, it } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { proxy } from "./proxy";

const originalFetch = globalThis.fetch;
const originalEnv = process.env.NODE_ENV;
afterEach(() => { globalThis.fetch = originalFetch; Object.assign(process.env, { NODE_ENV: originalEnv }); });

it("serves public contact anonymously and with existing auth without role lookup or redirection", async () => {
  Object.assign(process.env, { NODE_ENV: "production" });
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ role: "white_label_admin" }); };
  for (const cookie of ["", "talky_at=synthetic-token", "talky_sid=synthetic-session"]) {
    for (const path of ["/contact/", "/api/contact-enquiries"]) {
      const result = await proxy(new NextRequest(`https://app.example${path}`, { headers: { Cookie: cookie } }));
      assert.equal(result.headers.get("x-middleware-next"), "1");
      assert.equal(result.headers.get("location"), null);
    }
  }
  assert.equal(calls, 0, "Public contact must not consult auth or refresh");
});

it("does not expand contact permission to the authenticated contacts dashboard", async () => {
  Object.assign(process.env, { NODE_ENV: "production" });
  let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ role: "white_label_admin" }); };
  const result = await proxy(new NextRequest("https://app.example/contacts/", { headers: { Cookie: "talky_at=synthetic-token" } }));
  assert.ok(calls > 0);
  assert.equal(result.headers.get("location"), "https://app.example/white-label/dashboard/");
});
