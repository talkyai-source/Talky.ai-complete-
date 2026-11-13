import { test } from "node:test";
import assert from "node:assert/strict";
import { printCampaignsWithColumns } from "./campaign-performance-table";

// QA F1-18 (QA-UI-131): window.open("", "_blank", "noopener,noreferrer")
// returns null in browsers, so `if (!w) return;` exited and Export -> PDF
// never printed anything.
function withOpen(result: unknown, run: (calls: unknown[][]) => void) {
    const calls: unknown[][] = [];
    const original = window.open;
    window.open = ((...args: unknown[]) => {
        calls.push(args);
        return result as Window | null;
    }) as typeof window.open;
    try {
        run(calls);
    } finally {
        window.open = original;
    }
}

const CAMPAIGN = {
    id: "c1", name: "Q4 <b>renewals</b>", status: "running", total_leads: 10,
    calls_completed: 2, calls_failed: 1, created_at: "2026-11-01T00:00:00Z",
} as never;

test("Export -> PDF opens a print window, cuts its opener and prints", () => {
    const written: string[] = [];
    let printed = 0;
    const win = {
        opener: {} as unknown,
        document: { write: (html: string) => written.push(html), close() {} },
        focus() {},
        print() { printed += 1; },
    };
    withOpen(win, (calls) => {
        printCampaignsWithColumns([CAMPAIGN], ["id", "name"], "Last 7 days");
        assert.equal(calls.length, 1);
        assert.ok(!String(calls[0]?.[2] ?? "").includes("noopener"), "noopener makes window.open return null");
    });
    assert.equal(printed, 1);
    assert.equal(win.opener, null, "the opener link is cut before anything is written");
    assert.ok(written[0]?.includes("Q4 &lt;b&gt;renewals&lt;/b&gt;"), "names stay escaped");
});

test("a blocked pop-up is a no-op, not an error", () => {
    withOpen(null, () => {
        assert.doesNotThrow(() => printCampaignsWithColumns([CAMPAIGN], ["id"], "Today"));
    });
});
