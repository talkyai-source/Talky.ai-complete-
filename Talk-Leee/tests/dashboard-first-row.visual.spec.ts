import { expect, test } from "@playwright/test";

test.setTimeout(120_000);

async function stabilizePage(page: import("@playwright/test").Page) {
    await page.addInitScript(() => {
        const fixedNow = 1700000000000;
        Date.now = () => fixedNow;
        const rand = (() => {
            let s = 123456789 >>> 0;
            return () => {
                s = (s * 1664525 + 1013904223) >>> 0;
                return s / 0xffffffff;
            };
        })();
        Math.random = rand;
    });
}

const viewports = [
    { name: "mobile-360", width: 360, height: 800 },
    { name: "tablet-768", width: 768, height: 900 },
    { name: "desktop-1024", width: 1024, height: 900 },
    { name: "wide-1440", width: 1440, height: 900 },
] as const;

for (const vp of viewports) {
    test(`dashboard first row layout is pixel-stable (${vp.name})`, async ({ page }) => {
        await page.context().addCookies([{ name: "talklee_auth_token", value: "e2e-token", url: "http://127.0.0.1:3100" }]);
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

        const row = page.getByTestId("dashboard-kpi-row");
        await expect(row).toBeVisible();
        await page.waitForFunction(() => {
            const nodes = document.querySelectorAll('[data-testid="kpi-counter"]');
            return Array.from(nodes).some((n) => (n.textContent || "").trim() !== "0");
        });
        await page.waitForTimeout(900);
        await expect(row).toHaveScreenshot(`dashboard-kpi-row-${vp.name}.png`, { animations: "disabled" });
    });
}
