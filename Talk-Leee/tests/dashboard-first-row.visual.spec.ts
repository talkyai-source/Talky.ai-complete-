import { existsSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { mockDashboardApi, signInAsE2eUser } from "./support/session";

test.setTimeout(120_000);

// stabilizePage freezes Date.now to this instant. The dashboard fixtures are
// built around the same instant so the hourly rows fall inside the page's live
// window (its last 4 hours) and the Total Calls KPI is not empty.
const FROZEN_NOW_MS = 1700000000000;

async function stabilizePage(page: Page) {
    await page.addInitScript((fixedNow) => {
        Date.now = () => fixedNow;
        const rand = (() => {
            let s = 123456789 >>> 0;
            return () => {
                s = (s * 1664525 + 1013904223) >>> 0;
                return s / 0xffffffff;
            };
        })();
        Math.random = rand;
    }, FROZEN_NOW_MS);
}

const viewports = [
    { name: "mobile-360", width: 360, height: 800 },
    { name: "tablet-768", width: 768, height: 900 },
    { name: "desktop-1024", width: 1024, height: 900 },
    { name: "wide-1440", width: 1440, height: 900 },
] as const;

const kpiTitles = ["Total Calls", "Success Rate", "Active Calls", "Avg Duration"] as const;

// The signed-in dashboard with its backend answered in the browser (see
// support/session.ts: CI has no backend and the Next API route refuses /auth/*).
async function openDashboardKpiRow(page: Page, vp: (typeof viewports)[number]) {
    await signInAsE2eUser(page);
    await mockDashboardApi(page, { nowMs: FROZEN_NOW_MS });
    await stabilizePage(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await page.addStyleTag({
        content: `
                *, *::before, *::after {
                    transition-duration: 0s !important;
                    transition-delay: 0s !important;
                    animation-duration: 0s !important;
                    animation-delay: 0s !important;
                    caret-color: transparent !important;
                }
                .content-card:hover {
                    transform: none !important;
                }
            `,
    });
    await page.evaluate(() => document.fonts?.ready);

    // Dev mode compiles /dashboard and hydrates it on first use: allow for that
    // instead of the 5 s default of expect().
    const row = page.getByTestId("dashboard-kpi-row");
    await expect(row).toBeVisible({ timeout: 30_000 });
    // The counters count up once they scroll into view; on a phone the row sits
    // below the first-campaign checklist, out of view.
    await row.scrollIntoViewIfNeeded();
    await page.waitForFunction(
        () => {
            const nodes = document.querySelectorAll('[data-testid="kpi-counter"]');
            return Array.from(nodes).some((n) => (n.textContent || "").trim() !== "0");
        },
        undefined,
        { timeout: 30_000 },
    );
    await page.waitForTimeout(900);
    return row;
}

for (const vp of viewports) {
    // Runs everywhere: proves the first row renders from the mocked session and
    // summary (not the sign-in redirect, the loading skeleton or the error card).
    test(`dashboard first row renders its KPI cards (${vp.name})`, async ({ page }) => {
        const row = await openDashboardKpiRow(page, vp);
        for (const title of kpiTitles) await expect(row).toContainText(title);
        await expect(row.getByTestId("kpi-counter")).toHaveCount(kpiTitles.length);
    });

    // Pixel comparison. The committed baselines are all *-win32.png and the CI
    // runner is Linux, where Playwright would otherwise fail the test for a
    // missing snapshot (updateSnapshots "missing" writes it and fails). The job
    // never rewrites snapshots, so without a baseline for this project and
    // platform the comparison is skipped, visibly, instead of failing or
    // passing without having compared anything.
    test(`dashboard first row layout is pixel-stable (${vp.name})`, async ({ page }, testInfo) => {
        const name = `dashboard-kpi-row-${vp.name}.png`;
        const baseline = testInfo.snapshotPath(name, { kind: "screenshot" });
        const missing = !existsSync(baseline);
        const reason =
            `No baseline for project "${testInfo.project.name}" on ${process.platform}: ${path.basename(baseline)} is missing. ` +
            `Create it on that platform with: npx playwright test tests/dashboard-first-row.visual.spec.ts --project=${testInfo.project.name} --update-snapshots`;
        // The list reporter does not print skip reasons (only the HTML report does), so say it in the log too.
        if (missing) console.log(`[visual] skipped "${testInfo.title}": ${reason}`);
        test.skip(missing, reason);

        const row = await openDashboardKpiRow(page, vp);
        await expect(row).toHaveScreenshot(name, { animations: "disabled" });
    });
}
