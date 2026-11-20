import React from "react";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Call } from "@/lib/dashboard-api";
import type { exportCallHistory } from "@/lib/call-history-export";
import { CallHistoryDownload, describeCallHistoryFilters } from "./call-history-download";

afterEach(() => cleanup());

type Args = Parameters<typeof exportCallHistory>[0];
const makeCalls = (n: number): Call[] =>
    Array.from({ length: n }, (_, i) => ({ id: `call-${i}`, campaign_id: "", lead_id: "", phone_number: "+15550001111", status: "completed", created_at: "2026-11-06T10:00:00.000Z" }) as Call);
const brand = { name: "Acme", logoSrc: "/acme.svg" };
const known = { "call-0": { leadType: "hot" as const, notes: "n", form: { contact: "", interest: "", nextStep: "", completed: false }, updatedAt: "x" } };

function view(exportFn: typeof exportCallHistory, over: Partial<React.ComponentProps<typeof CallHistoryDownload>> = {}) {
    return render(
        <CallHistoryDownload calls={makeCalls(120)} complete knownWorkflow={known} filters={["Direction: incoming"]} timeZone="Europe/London" accountName="Acme Ltd" brand={brand} exportFn={exportFn} {...over} />,
    );
}
const trigger = () => screen.getByRole("button", { name: "Download" });

test("the menu opens with the two formats and the right ARIA", () => {
    view((async () => ({ fileName: "f", calls: 0 })) as typeof exportCallHistory);
    assert.equal(trigger().getAttribute("aria-haspopup"), "menu");
    assert.equal(trigger().getAttribute("aria-expanded"), "false");
    fireEvent.click(trigger());
    assert.equal(trigger().getAttribute("aria-expanded"), "true");
    assert.deepEqual(screen.getAllByRole("menuitem").map((el) => el.textContent), ["Excel (.xlsx)", "PDF (.pdf)"]);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    assert.equal(screen.queryByRole("menu") === null, true);
    assert.equal(document.activeElement === trigger(), true);
});

test("choosing Excel exports every call it was given with filters, zone, brand and known notes", async () => {
    const seen: Args[] = [];
    const fn = (async (args: Args) => { seen.push(args); return { fileName: "talk-lee-call-history-2026-11-21.xlsx", calls: 120 }; }) as typeof exportCallHistory;
    view(fn);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("menuitem", { name: "Excel (.xlsx)" }));
    await waitFor(() => assert.equal(seen.length, 1));
    assert.equal(seen[0].format, "xlsx");
    assert.equal(seen[0].calls.length, 120);
    assert.deepEqual(seen[0].meta, { accountName: "Acme Ltd", timeZone: "Europe/London", filters: ["Direction: incoming"], complete: true });
    assert.deepEqual(seen[0].brand, brand);
    assert.equal(seen[0].knownWorkflow, known);
    await screen.findByText("Downloaded talk-lee-call-history-2026-11-21.xlsx (120 calls)");
});

test("not complete: disabled and says Loading every call…; no calls: disabled", () => {
    const fn = (async () => ({ fileName: "f", calls: 0 })) as typeof exportCallHistory;
    view(fn, { complete: false });
    const button = screen.getByRole("button", { name: "Loading every call…" }) as HTMLButtonElement;
    assert.equal(button.disabled, true);
    cleanup();
    view(fn, { calls: [] });
    assert.equal((trigger() as HTMLButtonElement).disabled, true);
});

test("progress is shown in words, a second export cannot start, Cancel aborts quietly", async () => {
    let signal: AbortSignal | undefined;
    let calls = 0;
    const fn = ((args: Args) => {
        calls += 1;
        signal = args.signal;
        args.onProgress?.({ phase: "notes", done: 400, total: 1250 });
        return new Promise((_resolve, reject) => {
            args.signal?.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")));
        });
    }) as typeof exportCallHistory;
    view(fn);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("menuitem", { name: "PDF (.pdf)" }));
    await screen.findByRole("button", { name: "Reading notes 400 of 1,250" });
    assert.equal((screen.getByRole("button", { name: /Reading notes/ }) as HTMLButtonElement).disabled, true);
    fireEvent.click(screen.getByRole("button", { name: /Reading notes/ }));
    assert.equal(calls, 1);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(signal?.aborted, true));
    await screen.findByRole("button", { name: "Download" });
    assert.equal(screen.queryByRole("alert") === null, true);
    assert.equal(screen.queryByText(/could not be made/) === null, true);
});

test("a failure shows the sentence and Retry runs the same format again", async () => {
    const formats: string[] = [];
    const fn = (async (args: Args) => {
        formats.push(args.format);
        if (formats.length === 1) throw new Error("The server said no.");
        return { fileName: "ok.pdf", calls: 120 };
    }) as typeof exportCallHistory;
    view(fn);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("menuitem", { name: "PDF (.pdf)" }));
    const alert = await screen.findByRole("alert");
    assert.equal((alert.textContent ?? "").includes("The file could not be made. Try again."), true);
    assert.equal((alert.textContent ?? "").includes("The server said no."), true);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry" })); });
    await screen.findByText("Downloaded ok.pdf (120 calls)");
    assert.deepEqual(formats, ["pdf", "pdf"]);
});

test("progress is announced at most every 10 percent", async () => {
    let emit: (done: number) => void = () => {};
    let release: () => void = () => {};
    const fn = ((args: Args) => new Promise((resolve) => {
        emit = (done) => args.onProgress?.({ phase: "notes", done, total: 100 });
        release = () => resolve({ fileName: "f.xlsx", calls: 1 });
    })) as typeof exportCallHistory;
    const { container } = view(fn);
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("menuitem", { name: "Excel (.xlsx)" }));
    await screen.findByRole("button", { name: "Reading notes" });
    const live = () => container.querySelector('[role="status"]')?.textContent;
    await act(async () => { emit(0); });
    await act(async () => { for (let done = 1; done <= 9; done += 1) emit(done); });
    assert.equal(live(), "Reading notes 0 of 100");
    await screen.findByRole("button", { name: "Reading notes 9 of 100" });
    await act(async () => { emit(10); });
    assert.equal(live(), "Reading notes 10 of 100");
    await act(async () => { release(); });
});

test("describeCallHistoryFilters writes plain sentences, none when nothing is set", () => {
    assert.deepEqual(describeCallHistoryFilters({ direction: "all", filter: "all", search: " " }), []);
    assert.deepEqual(
        describeCallHistoryFilters({ direction: "inbound", filter: "leads", search: "0207", did: "+44", campaignName: "Sales" }),
        ["Direction: incoming", "Showing: leads only", "Search: 0207", "Phone number: +44", "Campaign: Sales"],
    );
});
