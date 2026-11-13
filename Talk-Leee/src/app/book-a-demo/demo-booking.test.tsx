import { describe, it, afterEach } from "node:test";
import assert from "node:assert";
import { render, screen, cleanup, fireEvent, act, waitFor } from "@testing-library/react";
import React from "react";
import { DemoBooking, DEMO_REQUEST_SAVED_KEY } from "./demo-booking";

const CALENDAR = "https://calendar.google.com/calendar/appointments/schedules/example?gv=true";
const BUSINESS = "1. Business name";
const SERVICES = "2. What services does your business offer?";
const IMPROVE = "3. What can be made better in your business using Talk-Lee AI?";
const calendar = () => screen.queryByTitle("Book a Demo calendar");

function fill() {
  fireEvent.change(screen.getByLabelText("Your name"), { target: { value: " Aisha Khan " } });
  fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "aisha@example.com" } });
  fireEvent.change(screen.getByLabelText(BUSINESS), { target: { value: "Khan Dental" } });
  fireEvent.change(screen.getByLabelText(SERVICES), { target: { value: "Family dentistry" } });
  fireEvent.change(screen.getByLabelText(IMPROVE), { target: { value: "Fewer missed reminders" } });
}
const submit = () => fireEvent.submit(screen.getByLabelText("Your name").closest("form")!);
const accepted = (requestId: string) =>
  Response.json({ status: "accepted", receipt_id: requestId, accepted_at: "2026-11-14T00:00:00Z" }, { status: 201 });

describe("DemoBooking", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    window.sessionStorage.clear();
  });

  it("offers a field for each of the three questions, and keeps the calendar closed until they are answered", () => {
    render(React.createElement(DemoBooking, { calendarUrl: CALENDAR }));
    assert.equal((screen.getByLabelText(BUSINESS) as HTMLInputElement).tagName, "INPUT");
    assert.equal((screen.getByLabelText(SERVICES) as HTMLTextAreaElement).tagName, "TEXTAREA");
    assert.equal((screen.getByLabelText(IMPROVE) as HTMLTextAreaElement).tagName, "TEXTAREA");
    assert.equal((screen.getByLabelText("Email address") as HTMLInputElement).type, "email");
    assert.equal(calendar(), null);
    assert.ok(screen.getByTestId("demo-calendar-closed"));
  });

  it("sends nothing and names each missing answer when the form is submitted empty", () => {
    let calls = 0;
    globalThis.fetch = async () => { calls++; return new Response(null, { status: 500 }); };
    render(React.createElement(DemoBooking, { calendarUrl: CALENDAR }));
    submit();
    assert.equal(calls, 0);
    assert.equal(screen.getAllByRole("alert").length, 5);
    assert.equal(screen.getByLabelText(BUSINESS).getAttribute("aria-invalid"), "true");
    assert.equal(document.activeElement, screen.getByLabelText("Your name"));
    assert.equal(calendar(), null);
  });

  it("saves the answers as one enquiry, then opens the calendar", async () => {
    const bodies: Record<string, string>[] = [];
    let url = "";
    globalThis.fetch = async (input, init) => {
      url = String(input);
      const body = JSON.parse(String(init?.body));
      bodies.push(body);
      return accepted(body.request_id);
    };
    render(React.createElement(DemoBooking, { calendarUrl: CALENDAR }));
    fill();
    await act(async () => { submit(); submit(); });
    await waitFor(() => assert.ok(calendar()));
    assert.equal(bodies.length, 1);
    assert.equal(url, "/api/contact-enquiries");
    assert.equal(bodies[0].name, "Aisha Khan");
    assert.equal(bodies[0].email, "aisha@example.com");
    assert.equal(bodies[0].company, "Khan Dental");
    assert.match(bodies[0].message, /^Book a Demo request\nServices offered: Family dentistry\nCould be better with Talk-Lee AI: Fewer missed reminders$/);
    assert.equal(calendar()!.getAttribute("src"), CALENDAR);
    assert.ok(screen.getByTestId("demo-answers-saved"));
    assert.equal(screen.queryByLabelText(BUSINESS), null);
    assert.equal(document.activeElement, screen.getByRole("heading", { name: "Choose a time" }));
    assert.equal(window.sessionStorage.getItem(DEMO_REQUEST_SAVED_KEY), "1");
  });

  it("still opens the calendar when the answers cannot be saved, and a retry reuses the same reference", async () => {
    const ids: string[] = [];
    let fail = true;
    globalThis.fetch = async (_input, init) => {
      const body = JSON.parse(String(init?.body));
      ids.push(body.request_id);
      return fail ? Response.json({ error: "Contact intake is unavailable", outcome: "unconfirmed" }, { status: 503 }) : accepted(body.request_id);
    };
    render(React.createElement(DemoBooking, { calendarUrl: CALENDAR }));
    fill();
    await act(async () => { submit(); });
    await waitFor(() => assert.ok(calendar()));
    assert.match(screen.getByTestId("demo-failure").textContent ?? "", /could not save your answers/i);
    assert.match(screen.getByTestId("demo-unsaved-note").textContent ?? "", /you can still choose a time/i);
    assert.equal((screen.getByLabelText(SERVICES) as HTMLTextAreaElement).value, "Family dentistry");
    assert.equal(screen.queryByTestId("demo-answers-saved"), null);
    assert.equal(window.sessionStorage.getItem(DEMO_REQUEST_SAVED_KEY), null);

    fail = false;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Try saving again" })); });
    await waitFor(() => assert.ok(screen.getByTestId("demo-answers-saved")));
    assert.equal(ids.length, 2);
    assert.equal(ids[0], ids[1]);
    assert.ok(calendar());
    assert.equal(screen.queryByTestId("demo-unsaved-note"), null);
  });

  it("keeps the calendar closed when the details are refused, so they are corrected first", async () => {
    globalThis.fetch = async () => Response.json({ error: "Contact request was not accepted" }, { status: 422 });
    render(React.createElement(DemoBooking, { calendarUrl: CALENDAR }));
    fill();
    await act(async () => { submit(); });
    await waitFor(() => assert.ok(screen.getByTestId("demo-failure")));
    assert.match(screen.getByTestId("demo-failure").textContent ?? "", /not accepted/i);
    assert.equal(calendar(), null);
    assert.equal((screen.getByLabelText(BUSINESS) as HTMLInputElement).value, "Khan Dental");
  });

  it("does not ask again after a reload once the answers were saved", () => {
    window.sessionStorage.setItem(DEMO_REQUEST_SAVED_KEY, "1");
    render(React.createElement(DemoBooking, { calendarUrl: CALENDAR }));
    assert.ok(calendar());
    assert.ok(screen.getByTestId("demo-answers-saved"));
    assert.equal(screen.queryByLabelText(BUSINESS), null);
  });
});
