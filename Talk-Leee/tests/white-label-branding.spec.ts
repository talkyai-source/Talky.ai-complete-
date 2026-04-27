import { test, expect } from "@playwright/test";

test.describe("White-label branding isolation", () => {
    test("Switches fully between partners without mixed branding", async ({ page }) => {
        test.setTimeout(120_000);
        const seen = new Set<string>();
        page.on("request", (req) => {
            const url = req.url();
            if (url.includes("/white-label/")) seen.add(url);
        });

        await page.goto("/white-label/acme/preview", { waitUntil: "domcontentloaded" });

        await expect(page.locator('[data-white-label-partner="acme"]')).toBeVisible();
        await expect(page.locator('img[alt="Acme"]').first()).toBeVisible();

        const acmePrimaryVar = await page
            .locator('[data-white-label-partner="acme"]')
            .evaluate((el) => getComputedStyle(el).getPropertyValue("--primary").trim().toLowerCase());
        expect(acmePrimaryVar).toBe("#2563eb");

        const acmeFaviconHrefs = await page
            .locator('head link[rel="icon"]')
            .evaluateAll((els) => els.map((el) => (el as HTMLLinkElement).href));
        expect(acmeFaviconHrefs.some((h) => h.includes("/white-label/acme/favicon.svg") && h.includes("wl=acme"))).toBe(true);

        seen.clear();
        await page.goto("/white-label/zen/preview", { waitUntil: "domcontentloaded" });

        await expect(page.locator('[data-white-label-partner="zen"]')).toBeVisible();
        await expect(page.locator('img[alt="Zen"]').first()).toBeVisible();
        await expect(page.locator('img[alt="Acme"]')).toHaveCount(0);

        const zenPrimaryVar = await page
            .locator('[data-white-label-partner="zen"]')
            .evaluate((el) => getComputedStyle(el).getPropertyValue("--primary").trim().toLowerCase());
        expect(zenPrimaryVar).toBe("#16a34a");

        const zenFaviconHrefs = await page
            .locator('head link[rel="icon"]')
            .evaluateAll((els) => els.map((el) => (el as HTMLLinkElement).href));
        expect(zenFaviconHrefs.some((h) => h.includes("/white-label/zen/favicon.svg") && h.includes("wl=zen"))).toBe(true);
        expect(zenFaviconHrefs.some((h) => h.includes("/white-label/acme/favicon.svg"))).toBe(false);

        const leaked = Array.from(seen).filter((u) => u.includes("/white-label/acme/"));
        expect(leaked).toEqual([]);
    });
});
