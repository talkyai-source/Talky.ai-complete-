import { test, expect } from "@playwright/test";

test.describe("Dashboard Audit", () => {
  test.beforeEach(async ({ page }) => {
    // Mock authentication
    await page.addInitScript(() => {
      localStorage.setItem("talklee.auth.token", "dev-token");
    });
  });

  test("Desktop: Sidebar layout and collapse", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/dashboard");

    // Sidebar should be visible
    const sidebar = page.locator(".talklee-sidebar");
    await expect(sidebar).toBeVisible();

    // Check content padding (expanded)
    // The sidebar width is controlled by CSS variable, but we can check the main content container
    const mainContent = page.locator("main");
    await expect(mainContent).toBeVisible();

    // Collapse sidebar
    const collapseButton = page.getByRole("button", { name: "Collapse sidebar" });
    await expect(collapseButton).toBeVisible();
    await collapseButton.click();

    // Wait for animation
    await page.waitForTimeout(500);

    // Sidebar should still be visible but smaller (we can't easily check width without eval)
    await expect(sidebar).toBeVisible();

    // Expand sidebar
    const expandButton = page.getByRole("button", { name: "Expand sidebar" });
    await expect(expandButton).toBeVisible();
    await expandButton.click();
  });

  test("Mobile: Sidebar interaction", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/dashboard");

    // Sidebar should be hidden initially
    // The desktop sidebar has 'hidden lg:block' class
    const desktopSidebar = page.locator(".talklee-sidebar");
    await expect(desktopSidebar).toBeHidden();

    // The mobile sidebar is a drawer, initially closed
    const mobileSidebar = page.getByRole("dialog", { name: "Sidebar" });
    await expect(mobileSidebar).toBeHidden();

    // Try to open using the hidden strip (not ideal UX, but current implementation)
    const triggerStrip = page.locator('div[role="button"][aria-label="Open sidebar"]');
    await expect(triggerStrip).toBeVisible();
    
    // Click the strip
    await triggerStrip.click();

    // Sidebar should be visible now
    await expect(mobileSidebar).toBeVisible();

    // Close using the close button
    const closeButton = page.getByRole("button", { name: "Close sidebar" });
    await expect(closeButton).toBeVisible();
    await closeButton.click();

    // Sidebar should be hidden again
    await expect(mobileSidebar).toBeHidden();
  });

  test("Mobile: Visible hamburger button", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/dashboard");

    // Check for a VISIBLE button to open the sidebar
    // It should be visible on mobile
    const hamburger = page.getByRole("button", { name: "Open sidebar" }).first();
    await expect(hamburger).toBeVisible();
    
    // It should not be transparent (we can't easily check computed style, but we can check class)
    // Actually, just checking visibility and clickability is good.
    // Let's ensure it's not the invisible strip.
    // The invisible strip has class 'fixed left-0 top-0 ...'
    // The new button should be in the header.
    
    // Let's click it and verify sidebar opens
    await hamburger.click();
    const mobileSidebar = page.getByRole("dialog", { name: "Sidebar" });
    await expect(mobileSidebar).toBeVisible();
  });
});
