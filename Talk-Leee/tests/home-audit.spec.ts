import { test, expect } from "@playwright/test";

test.describe("Home Page Audit", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
  });

  test("Mobile: No horizontal scroll and title visibility", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.waitForTimeout(2000);

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBe(clientWidth);

    // Look for the H1 or the main title components
    // The hero title is split into "AI VOICE" and "DIALER"
    const heroTitleA = page.locator("text=AI VOICE").first();
    const heroTitleB = page.locator("text=DIALER").first();
    
    // We expect them to be visible. 
    // Note: The previous failure was because "AI VOICE" matched a link.
    // Let's match by class if possible, or filter by heading context.
    // But since we don't know exact structure of lazy loaded component container (it might not be h1),
    // let's try to be more specific.
    // In HelixHero: <motion.span ...>{headlineA}</motion.span>
    
    // Use heading locator which is more semantic and likely what we want
    // Note: The page initially loads a placeholder with H1 "AI VOICE" and H2 "DIALER".
    // The real component has H1 containing both "AI" and "VOICE DIALER" (split in spans).
    // We wait for the real component by checking for "DIALER" in H1.
    const heading = page.getByRole("heading", { level: 1 }).filter({ hasText: "DIALER" });
    await expect(heading).toBeVisible({ timeout: 10000 });
    await expect(heading).toContainText("AI");
  });

  test("Tablet/Desktop: Packages Grid Layout", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 }); // Use a wider screen to ensure 4 cols
    
    const packagesSection = page.locator("#packages");
    await packagesSection.scrollIntoViewIfNeeded();
    await page.waitForTimeout(2000);

    const cards = packagesSection.locator(".home-packages-card");
    const count = await cards.count();
    expect(count).toBe(4);

    const box1 = await cards.nth(0).boundingBox();
    const box4 = await cards.nth(3).boundingBox();

    if (box1 && box4) {
      console.log(`Box1 Y: ${box1.y}, Box4 Y: ${box4.y}`);
      expect(Math.abs(box1.y - box4.y)).toBeLessThan(10); // Allow small diff
    }
  });

  test("Mobile Menu Interaction", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    
    const menuToggle = page.locator("summary.home-menu-toggle");
    await expect(menuToggle).toBeVisible();
    
    // Force click if needed, or check if covered
    await menuToggle.click({ force: true });
    
    // Check if details is open
    const details = page.locator("details.group");
    await expect(details).toHaveAttribute("open", "");
    
    // Check overlay
    const overlay = page.locator(".home-mobile-overlay");
    // Wait for animation/transition
    await page.waitForTimeout(500);
    await expect(overlay).toBeVisible();
    
    // Close using the close button inside the panel
    const closeButton = page.getByRole("button", { name: "Close navigation menu" });
    await expect(closeButton).toBeVisible();
    await closeButton.click();
    await expect(details).not.toHaveAttribute("open");
  });

  test("Desktop: Navbar anchors and hover states", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(500);

    const servicesLink = page.getByRole("link", { name: "Services" }).first();
    const beforeColor = await servicesLink.evaluate((el) => getComputedStyle(el).color);
    await servicesLink.hover();
    const afterColor = await servicesLink.evaluate((el) => getComputedStyle(el).color);
    expect(beforeColor).not.toBe(afterColor);

    await servicesLink.click();
    await page.waitForTimeout(400);
    const servicesSection = page.locator("#services");
    await expect(servicesSection).toBeVisible();
    const servicesBox = await servicesSection.boundingBox();
    expect(servicesBox).not.toBeNull();
    if (servicesBox) {
      expect(servicesBox.top).toBeGreaterThanOrEqual(-2);
      expect(servicesBox.top).toBeLessThanOrEqual(120);
    }

    const packagesLink = page.getByRole("link", { name: "Packages" }).first();
    await packagesLink.click();
    await page.waitForTimeout(400);
    const packagesSection = page.locator("#packages");
    await expect(packagesSection).toBeVisible();
    const packagesBox = await packagesSection.boundingBox();
    expect(packagesBox).not.toBeNull();
    if (packagesBox) {
      expect(packagesBox.top).toBeGreaterThanOrEqual(-2);
      expect(packagesBox.top).toBeLessThanOrEqual(120);
    }

    const contactLink = page.getByRole("link", { name: "Contact" }).first();
    await contactLink.click();
    await page.waitForTimeout(400);
    const contactSection = page.locator("#contact");
    await expect(contactSection).toBeVisible();
    const contactBox = await contactSection.boundingBox();
    expect(contactBox).not.toBeNull();
    if (contactBox) {
      expect(contactBox.top).toBeGreaterThanOrEqual(-2);
      expect(contactBox.top).toBeLessThanOrEqual(140);
    }
  });
});
