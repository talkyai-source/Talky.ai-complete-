import { afterEach, it } from "node:test";
import assert from "node:assert/strict";
import { ContactSubmissionError, submitContactEnquiry } from "./contact-enquiries";
import { CONTACT_DRAFT_KEY, CONTACT_DRAFT_TTL_MS, loadContactDraft } from "./contact-enquiry-draft";
const originalFetch = globalThis.fetch;
const payload = { request_id: "e060104a-883d-4f68-a2ca-73c091baef8a", name: "Visitor", email: "visitor@example.com", company: "", message: "Hello" };
afterEach(() => { globalThis.fetch = originalFetch; window.sessionStorage.clear(); });

it("anonymous submission never sends cookies/tokens or refreshes on 401", async () => {
  let calls = 0;
  const sent: { url?: unknown; init?: RequestInit } = {};
  globalThis.fetch = async (url, init) => {
    calls++; sent.url = url; sent.init = init;
    return Response.json({}, { status: 401 });
  };
  await assert.rejects(submitContactEnquiry(payload), /could not confirm/);
  assert.equal(calls, 1);
  assert.equal(sent.url, "/api/contact-enquiries");
  assert.equal(sent.init?.credentials, "omit");
  assert.deepEqual(sent.init?.headers, { "Content-Type": "application/json" });
});

it("aborts a lost response without retrying or mutating request identity", async () => {
  let calls = 0;
  let sentPayload: unknown;
  globalThis.fetch = async (_url, init) => {
    calls++; sentPayload = JSON.parse(String(init?.body));
    return new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))));
  };
  await assert.rejects(submitContactEnquiry(payload, 5), /could not confirm/);
  assert.equal(calls, 1);
  assert.deepEqual(sentPayload, payload);
});

it("expires an unsubmitted draft without retaining personal data", () => {
  window.sessionStorage.setItem(CONTACT_DRAFT_KEY, JSON.stringify({ version: 1, savedAt: Date.now() - CONTACT_DRAFT_TTL_MS - 1, draft: payload }));
  assert.equal(loadContactDraft().saved, undefined);
  assert.equal(window.sessionStorage.getItem(CONTACT_DRAFT_KEY), null);
});

it("an unrelated valid receipt is still an unconfirmed outcome", async () => {
  globalThis.fetch = async () => Response.json({ status: "accepted", receipt_id: "6640abf3-f279-4b1f-9998-cfda6a6df383", accepted_at: "2026-10-04T00:00:00Z" });
  await assert.rejects(submitContactEnquiry(payload), /could not confirm/);
});

it("matches the same UUID regardless of letter case", async () => {
  globalThis.fetch = async () => Response.json({ status: "accepted", receipt_id: payload.request_id, accepted_at: "2026-10-04T00:00:00Z" });
  const accepted = await submitContactEnquiry({ ...payload, request_id: payload.request_id.toUpperCase() });
  assert.equal(accepted.receipt_id, payload.request_id);
});

for (const status of [400, 413, 415, 422]) {
  it(`allows correction after known request rejection ${status}`, async () => {
    globalThis.fetch = async () => Response.json({}, { status });
    await assert.rejects(submitContactEnquiry(payload), (error: unknown) => error instanceof ContactSubmissionError && error.canEdit);
  });
}

it("keeps an expired unknown reference blocked in memory even if storage becomes read-only", () => {
  window.sessionStorage.setItem(CONTACT_DRAFT_KEY, JSON.stringify({ version: 1, savedAt: Date.now() - CONTACT_DRAFT_TTL_MS - 1, draft: payload, submission: payload }));
  const proto = Object.getPrototypeOf(window.sessionStorage);
  const originalSet = proto.setItem;
  try {
    proto.setItem = () => { throw new Error("storage now read-only"); };
    const result = loadContactDraft();
    assert.equal(result.saved?.expiredRequestId, payload.request_id);
    assert.equal(result.storageAvailable, false);
    assert.equal(result.saved?.draft, undefined);
  } finally { proto.setItem = originalSet; }
});
