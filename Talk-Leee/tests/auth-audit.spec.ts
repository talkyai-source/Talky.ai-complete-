import { test, expect } from "@playwright/test";

test.describe("Auth Pages Audit", () => {
  test("Login Page: Layout and Interactions", async ({ page }) => {
    await page.goto("/auth/login");

    // Check visibility of key elements
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByRole("button", { name: "Send Verification Code" })).toBeVisible();
    await expect(page.getByText("New to Talk-Lee?")).toBeVisible();

    // Check responsiveness (mobile)
    await page.setViewportSize({ width: 375, height: 667 });
    
    // Ensure form is still visible and centered (roughly)
    // The card is the main container. It has 'rounded-xl border' etc. 
    // We can find it by its content.
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    
    // Check input interaction
    await page.fill('input[type="email"]', "test@example.com");
    await expect(page.locator('input[value="test@example.com"]')).toBeVisible();
  });

  test("Register Page: Layout and Interactions", async ({ page }) => {
    await page.goto("/auth/register");

    // Check visibility of key elements
    // The heading is "Create your account" in step "form"
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
    await expect(page.getByLabel("Your Name")).toBeVisible();
    await expect(page.getByLabel("Work Email")).toBeVisible();
    await expect(page.getByLabel("Business Name")).toBeVisible();
    await expect(page.getByRole("button", { name: "Get Started" })).toBeVisible();

    // Check responsiveness (mobile)
    await page.setViewportSize({ width: 375, height: 667 });

    // Check form fields stack correctly
    const nameInput = page.getByLabel("Your Name");
    const emailInput = page.getByLabel("Work Email");
    
    // Basic check: elements should be visible
    await expect(nameInput).toBeVisible();
    await expect(emailInput).toBeVisible();
  });
});
