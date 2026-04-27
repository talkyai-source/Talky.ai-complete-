import { test, expect } from "@playwright/test";

test.describe("Home Page Audit", () => {
  test("Mobile: No horizontal scroll and title visibility", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBe(clientWidth);

    const heading = page.getByRole("heading", { level: 1 }).first();
    await expect(heading).toBeVisible({ timeout: 10000 });
  });

  test("Tablet/Desktop: Services Grid Layout", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/", { waitUntil: "domcontentloaded" });

    const servicesSection = page.locator("#services");
    await servicesSection.scrollIntoViewIfNeeded();
    await expect(servicesSection).toBeVisible();

    const cards = servicesSection.locator(".home-services-card");
    const count = await cards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("Mobile Menu Interaction", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/", { waitUntil: "domcontentloaded" });

    const menuToggle = page.getByLabel("Open navigation menu").first();
    await expect(menuToggle).toBeVisible();
    await menuToggle.click();

    const panel = page.getByRole("menu", { name: "Mobile" });
    await expect(panel).toBeVisible();

    const closeButton = page.getByLabel("Close navigation menu").first();
    await expect(closeButton).toBeVisible();
    await closeButton.click();
    await expect(panel).toBeHidden();
  });

  test("Desktop: Navbar anchors and hover states", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);

    const faqLink = page.getByRole("link", { name: "FAQ" }).first();
    await faqLink.click();
    await page.waitForFunction(() => window.location.hash === "#faq");
    await page.waitForFunction(() => {
      const el = document.getElementById("faq");
      if (!el) return false;
      return el.getBoundingClientRect().top <= 200;
    });

    const contactLink = page.getByRole("link", { name: "Contact" }).first();
    await contactLink.click();
    await page.waitForFunction(() => window.location.hash === "#contact");
    await page.waitForFunction(() => {
      const el = document.getElementById("contact");
      if (!el) return false;
      return el.getBoundingClientRect().top <= 200;
    });
  });
});
