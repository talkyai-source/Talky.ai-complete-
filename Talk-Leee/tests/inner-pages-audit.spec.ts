import { test, expect } from "@playwright/test";

test.describe("Inner Pages Audit", () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.addInitScript(() => {
      localStorage.setItem("talklee.auth.token", "dev-token");
    });
  });

  const pagesToAudit = [
    { path: "/campaigns", title: "Campaigns", selector: "h1" },
    { path: "/contacts", title: "Contacts", selector: "h1" },
    { path: "/analytics", title: "Analytics", selector: "h1" },
    { path: "/settings", title: "Settings", selector: "h1" },
  ];

  for (const { path, title, selector } of pagesToAudit) {
    test(`Desktop: ${title} Page Layout`, async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 800 });
      await page.goto(path);

      // Check title visibility
      // Some pages might use different title structure, but H1 is common
      // Adjust selector if needed based on actual implementation
      const heading = page.locator(selector).first();
      await expect(heading).toBeVisible({ timeout: 10000 });
      
      // Check for main content container
      const main = page.locator("main");
      await expect(main).toBeVisible();

      // Check no horizontal scroll
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
      expect(scrollWidth).toBe(clientWidth);
    });

    test(`Mobile: ${title} Page Responsiveness`, async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 667 });
      await page.goto(path);

      // Check title visibility
      const heading = page.locator(selector).first();
      await expect(heading).toBeVisible({ timeout: 10000 });

      // Check for hamburger menu (from dashboard layout)
      const menuButton = page.getByRole("button", { name: "Open sidebar" }).first();
      await expect(menuButton).toBeVisible();

      // Check no horizontal scroll
      const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
      const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
      expect(scrollWidth).toBe(clientWidth);
    });
  }
});
