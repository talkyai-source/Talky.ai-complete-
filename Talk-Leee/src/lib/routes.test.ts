import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

function read(relFromSrc: string) {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const src = path.join(here, "..");
    const p = path.join(src, relFromSrc);
    return readFileSync(p, "utf8");
}

test("new routes use DashboardLayout", () => {
    assert.match(read("app/settings/connectors/page.tsx"), /<DashboardLayout/);
    assert.match(read("app/assistant/page.tsx"), /<DashboardLayout/);
    assert.match(read("app/assistant/actions/page.tsx"), /<DashboardLayout/);
    assert.match(read("app/assistant/meetings/page.tsx"), /<DashboardLayout/);
    const assistantReminders = read("app/assistant/reminders/page.tsx");
    if (/<DashboardLayout/.test(assistantReminders)) {
        assert.match(assistantReminders, /<DashboardLayout/);
    } else {
        assert.match(assistantReminders, /from\s+"@\/app\/reminders\/page"/);
        assert.match(assistantReminders, /<RemindersPage\s*\/>/);
    }
    assert.match(read("app/meetings/page.tsx"), /<DashboardLayout/);
    assert.match(read("app/reminders/page.tsx"), /<DashboardLayout/);
});
