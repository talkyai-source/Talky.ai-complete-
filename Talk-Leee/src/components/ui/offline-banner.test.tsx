import { afterEach, mock, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, render, screen } from "@testing-library/react";
import { OfflineBanner } from "./offline-banner";

// QA F1-27 (QA-UI-093): there was no offline indicator, so the first sign of
// a dropped connection was a failed save. Signed-in pages now say so.
let online = true;
afterEach(() => { cleanup(); mock.restoreAll(); online = true; });

function goOnline(next: boolean) {
    online = next;
    act(() => { window.dispatchEvent(new window.Event(next ? "online" : "offline")); });
}

test("the banner appears while the browser is offline and leaves when it reconnects", () => {
    mock.getter(Object.getPrototypeOf(window.navigator), "onLine", () => online);
    render(<OfflineBanner />);
    assert.equal(screen.queryByRole("status"), null);
    goOnline(false);
    assert.match(screen.getByRole("status").textContent ?? "", /You're offline\. Changes can't be saved until you reconnect\./);
    goOnline(true);
    assert.equal(screen.queryByRole("status"), null);
});

test("every signed-in page shows the banner (the dashboard layout mounts it)", () => {
    const layout = readFileSync(path.join(process.cwd(), "src/components/layout/dashboard-layout.tsx"), "utf8");
    assert.match(layout, /<SuspensionBanner \/>\s*<OfflineBanner \/>/);
});
