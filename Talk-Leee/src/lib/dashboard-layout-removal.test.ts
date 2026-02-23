import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

test("dashboard layout removes theme toggle and removes global sidebar toggle", () => {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const dashboardLayoutPath = path.join(here, "..", "components", "layout", "dashboard-layout.tsx");
    const contents = readFileSync(dashboardLayoutPath, "utf8");

    assert.doesNotMatch(contents, /Switch to (dark|light) theme/);
    assert.doesNotMatch(contents, /toggleTheme/);
    assert.doesNotMatch(contents, /onClick=\{toggleTheme\}/);

    assert.doesNotMatch(contents, /<GlobalSidebarToggle\s*\/>/);
    assert.match(contents, /<Sidebar\b[^>]*\/>/);
});
