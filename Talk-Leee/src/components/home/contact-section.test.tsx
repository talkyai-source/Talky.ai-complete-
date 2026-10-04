
import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup, fireEvent, act, waitFor } from "@testing-library/react";
import React from "react";
import { ContactSection } from "./contact-section";
import { CONTACT_DRAFT_KEY, CONTACT_DRAFT_TTL_MS } from "@/lib/contact-enquiry-draft";

const receipt = { status: "accepted", receipt_id: "e060104a-883d-4f68-a2ca-73c091baef8a", accepted_at: "2026-10-04T00:00:00Z" };
function fill() {
  fireEvent.change(screen.getByLabelText("Full Name"), { target: { value: " Test Visitor " } });
  fireEvent.change(screen.getByLabelText("Email Address"), { target: { value: "visitor@example.com" } });
  fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Please explain your plans." } });
}
const submit = () => fireEvent.submit(screen.getByLabelText("Full Name").closest("form")!);

describe("ContactSection", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    window.sessionStorage.clear();
  });

  it("renders the contact form and info section", () => {
    render(React.createElement(ContactSection));
    
    // Check for header
    assert.ok(screen.getByText("Contact Us"));
    
    // Check for form fields
    assert.ok(screen.getByLabelText("Full Name"));
    assert.ok(screen.getByLabelText("Email Address"));
    assert.ok(screen.getByLabelText("Company"));
    assert.ok(screen.getByLabelText("Message"));
    
    // Check for contact info
    assert.ok(screen.getByText("Get in Touch"));
    assert.ok(screen.getByText("contact@talk-lee.com"));
  });

  it("submits once despite synchronous duplicate events and waits for a durable receipt", async () => {
    let calls = 0;
    let resolve!: (response: Response) => void;
    let requestId = "";
    globalThis.fetch = async (_url, init) => { calls++; requestId = JSON.parse(String(init?.body)).request_id; return new Promise<Response>((done) => { resolve = done; }); };
    render(React.createElement(ContactSection));
    fireEvent.change(screen.getByLabelText("Full Name"), { target: { value: "Test Visitor" } });
    fireEvent.change(screen.getByLabelText("Email Address"), { target: { value: "visitor@example.com" } });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Please explain your plans." } });
    const form = screen.getByRole("button", { name: "Submit" }).closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    assert.equal(calls, 1);
    assert.equal(screen.queryByTestId("success-message"), null);
    assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).value, "Please explain your plans.");
    await act(async () => resolve(Response.json({ ...receipt, receipt_id: requestId }, { status: 201 })));
    assert.match(screen.getByTestId("success-message").textContent ?? "", /received/i);
    assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).value, "");
    assert.equal(window.sessionStorage.getItem(CONTACT_DRAFT_KEY), null);
  });

  for (const failure of ["network", "invalid receipt", "503", "429", "409"]) {
    it(`retains and retries the identical payload after ${failure}`, async () => {
      const requests: string[] = [];
      globalThis.fetch = async (_url, init) => {
        requests.push(String(init?.body));
        if (requests.length === 2) return Response.json({ ...receipt, receipt_id: JSON.parse(requests[0]!).request_id }, { status: 200 });
        if (failure === "network") throw new TypeError("lost response");
        if (failure === "invalid receipt") return Response.json({ status: "accepted" });
        return Response.json({ error: "unavailable" }, { status: Number(failure) });
      };
      render(React.createElement(ContactSection)); fill(); submit();
      await waitFor(() => assert.ok(screen.getByRole("alert")));
      assert.equal(screen.queryByTestId("success-message"), null);
      assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).value, "Please explain your plans.");
      fireEvent.change(screen.getByLabelText("Message"), { target: { value: "changed after unknown" } });
      assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).value, "Please explain your plans.");
      fireEvent.click(screen.getByRole("button", { name: "Retry saved message" }));
      await waitFor(() => assert.ok(screen.getByTestId("success-message")));
      assert.equal(requests[0], requests[1]);
      assert.equal(JSON.parse(requests[0]!).name, "Test Visitor");
    });
  }

  it("restores both an unsent draft and an unknown request on reload without auto-submitting", async () => {
    const requests: string[] = [];
    globalThis.fetch = async (_url, init) => { requests.push(String(init?.body)); throw new TypeError("lost response"); };
    render(React.createElement(ContactSection)); fill(); cleanup();
    render(React.createElement(ContactSection));
    assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).value, "Please explain your plans.");
    assert.equal(requests.length, 0);
    submit();
    await waitFor(() => assert.ok(screen.getByRole("alert")));
    cleanup(); render(React.createElement(ContactSection));
    assert.equal(requests.length, 1);
    globalThis.fetch = async (_url, init) => { requests.push(String(init?.body)); return Response.json({ ...receipt, receipt_id: JSON.parse(requests[0]!).request_id }); };
    fireEvent.click(screen.getByRole("button", { name: "Retry saved message" }));
    await waitFor(() => assert.ok(screen.getByTestId("success-message")));
    assert.equal(requests[0], requests[1]);
  });

  it("supports safe in-memory retry when session storage is blocked", async () => {
    const proto = Object.getPrototypeOf(window.sessionStorage);
    const originalGet = proto.getItem, originalSet = proto.setItem;
    const requests: string[] = [];
    try {
      proto.getItem = () => { throw new Error("blocked"); };
      proto.setItem = () => { throw new Error("blocked"); };
      globalThis.fetch = async (_url, init) => { requests.push(String(init?.body)); throw new TypeError("offline"); };
      render(React.createElement(ContactSection)); fill(); submit();
      await waitFor(() => assert.ok(screen.getByRole("alert")));
      assert.ok(screen.getByText(/browser cannot save this draft/));
      fireEvent.click(screen.getByRole("button", { name: "Retry saved message" }));
      await waitFor(() => assert.equal(requests.length, 2));
      assert.equal(requests[0], requests[1]);
    } finally { proto.getItem = originalGet; proto.setItem = originalSet; }
  });

  it("removes expired personal data but does not create a new ID for an unknown request", () => {
    let calls = 0;
    globalThis.fetch = async () => { calls++; return Response.json(receipt); };
    const request = { request_id: receipt.receipt_id, name: "Old Visitor", email: "old@example.com", company: "", message: "Old message" };
    window.sessionStorage.setItem(CONTACT_DRAFT_KEY, JSON.stringify({ version: 1, savedAt: Date.now() - CONTACT_DRAFT_TTL_MS - 1, draft: request, submission: request }));
    render(React.createElement(ContactSection));
    assert.ok(screen.getByRole("alert").textContent?.includes("expired"));
    submit(); assert.equal(calls, 0);
    assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).value, "");
    assert.ok(!window.sessionStorage.getItem(CONTACT_DRAFT_KEY)?.includes("old@example.com"));
    assert.ok(window.sessionStorage.getItem(CONTACT_DRAFT_KEY)?.includes(receipt.receipt_id));
  });

  it("keeps a verified receipt successful even when clearing the local draft fails", async () => {
    const proto = Object.getPrototypeOf(window.sessionStorage);
    const originalRemove = proto.removeItem;
    try {
      proto.removeItem = () => { throw new Error("cannot clear storage"); };
      globalThis.fetch = async (_url, init) => Response.json({ ...receipt, receipt_id: JSON.parse(String(init?.body)).request_id });
      render(React.createElement(ContactSection)); fill(); submit();
      await waitFor(() => assert.ok(screen.getByTestId("success-message")));
      assert.equal(screen.queryByRole("alert"), null);
      assert.ok(screen.getByText(/Receipt is confirmed, but/));
      assert.ok(screen.getByRole("button", { name: "Submit" }));
    } finally { proto.removeItem = originalRemove; }
  });

  it("validates trimmed required fields and bounds before requesting", () => {
    let calls = 0;
    globalThis.fetch = async () => { calls++; return Response.json(receipt); };
    render(React.createElement(ContactSection)); fill();
    fireEvent.change(screen.getByLabelText("Company"), { target: { value: "x".repeat(121) } });
    submit(); assert.equal(calls, 0); assert.ok(screen.getByRole("alert"));
    assert.equal((screen.getByLabelText("Full Name") as HTMLInputElement).maxLength, 120);
    assert.equal((screen.getByLabelText("Email Address") as HTMLInputElement).maxLength, 254);
    assert.equal((screen.getByLabelText("Message") as HTMLTextAreaElement).maxLength, 500);
    assert.equal(screen.queryByText(/555|123 AI Street/), null);
  });
});
