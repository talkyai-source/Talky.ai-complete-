import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import type { Call } from "@/lib/dashboard-api";
import { DialAgainButton } from "./dial-again-button";

const request = api.request;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); api.request = request; });
const call = { id: "call-1", campaign_id: "campaign-1", lead_id: "lead-1", phone_number: "+14165550123", status: "completed", direction: "outbound" } as Call;
function mount(overrides: Partial<Call> = {}) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><DialAgainButton call={{ ...call, ...overrides }} /></QueryClientProvider>);
}

test("checking a route cannot start a call and a blocked route disables confirmation", async () => {
    const writes: string[] = [];
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method === "POST") writes.push(options.path);
        return { eligible: false, reason: "Selected SIP trunk is not ready", phone_number: call.phone_number, caller_id: "+442079460000" } as T;
    };
    mount();
    fireEvent.click(screen.getByRole("button", { name: `Dial again ${call.phone_number}` }));
    await screen.findByText("Selected SIP trunk is not ready");
    assert.equal((screen.getByRole("button", { name: "Call again" }) as HTMLButtonElement).disabled, true);
    assert.ok(screen.getByText("+442079460000"));
    assert.deepEqual(writes, []);
});

test("ready route submits once and shows the queue receipt without claiming connection", async () => {
    let resolve!: (value: unknown) => void;
    const pending = new Promise((done) => { resolve = done; });
    let posts = 0;
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method === "POST") { posts++; return await pending as T; }
        return { eligible: true, phone_number: call.phone_number, caller_id: "+442079460000" } as T;
    };
    mount();
    fireEvent.click(screen.getByRole("button", { name: `Dial again ${call.phone_number}` }));
    const button = await screen.findByRole("button", { name: "Call again" });
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
    fireEvent.click(button);
    fireEvent.click(button);
    assert.equal(posts, 1);
    await act(async () => resolve({ job_id: "job-1", status: "queued", message: "Call queued." }));
    await screen.findByText(/Call queued.*has not necessarily connected/);
    assert.equal(screen.queryByRole("button", { name: "Call again" }), null);
});

test("inbound and active calls have no reattempt control", () => {
    mount({ direction: "inbound" });
    assert.equal(screen.queryByRole("button"), null);
    cleanup();
    mount({ status: "in_progress" });
    assert.equal(screen.queryByRole("button"), null);
});

test("a pending queue handoff can explicitly retry the same saved request", async () => {
    const posts: string[] = [];
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method === "POST") {
            posts.push(options.path);
            return { job_id: "same-job", status: posts.length === 1 ? "pending" : "queued", message: posts.length === 1 ? "Queue handoff pending." : "Saved request queued." } as T;
        }
        return { eligible: true, phone_number: call.phone_number } as T;
    };
    mount();
    fireEvent.click(screen.getByRole("button", { name: `Dial again ${call.phone_number}` }));
    const button = screen.getByRole("button", { name: "Call again" });
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
    fireEvent.click(button);
    await screen.findByRole("button", { name: "Retry saved request" });
    assert.equal(posts.length, 1); // No automatic repeat after a pending response.
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: `Dial again ${call.phone_number}` }));
    fireEvent.click(screen.getByRole("button", { name: "Retry saved request" }));
    await screen.findByText(/Saved request queued/);
    assert.deepEqual(posts, ["/calls/call-1/redial", "/calls/call-1/redial"]);
    assert.equal(screen.queryByRole("button", { name: "Retry saved request" }), null);
});

test("post rejection stays visible and cannot claim the call was queued", async () => {
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method === "POST") throw new Error("Contact opted out before this request.");
        return { eligible: true, phone_number: call.phone_number } as T;
    };
    mount();
    fireEvent.click(screen.getByRole("button", { name: `Dial again ${call.phone_number}` }));
    const button = screen.getByRole("button", { name: "Call again" });
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
    fireEvent.click(button);
    await screen.findByText("Contact opted out before this request.");
    assert.equal(screen.queryByText(/queue receipt/), null);
});

test("an existing receipt displays its saved destination rather than a changed preview", async () => {
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method === "POST") return { job_id: "saved-job", status: "queued", phone_number: "+14165550199", message: "This redial request already exists." } as T;
        return { eligible: true, phone_number: call.phone_number, caller_id: "+442079460000" } as T;
    };
    mount();
    fireEvent.click(screen.getByRole("button", { name: `Dial again ${call.phone_number}` }));
    const button = screen.getByRole("button", { name: "Call again" });
    await waitFor(() => assert.equal((button as HTMLButtonElement).disabled, false));
    fireEvent.click(button);
    await screen.findByText("+14165550199");
    assert.ok(screen.getByText(/Caller ID at last check:/));
});
