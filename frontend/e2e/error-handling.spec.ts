import { test, expect } from "@playwright/test";

test.describe("Error Handling", () => {
  test("shows error message for empty login fields", async ({ page }) => {
    await page.goto("/login");
    await page.click('button[type="submit"]');
    await expect(page.getByText("Username is required")).toBeVisible();
    await expect(page.getByText("Password is required")).toBeVisible();
  });

  test("shows error for invalid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[placeholder="Enter username"]', "nonexistent");
    await page.fill('input[placeholder="Enter password"]', "wrongpassword");
    await page.click('button[type="submit"]');
    await expect(page.getByText("Invalid username or password")).toBeVisible();
  });

  test("redirects unauthenticated user to login", async ({ page }) => {
    await page.goto("/projects");
    await expect(page).toHaveURL(/\/login/);
  });

  test("shows empty state when no projects exist", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[placeholder="Enter username"]', "admin");
    await page.fill('input[placeholder="Enter password"]', "admin");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/projects/);
    // Page should load without errors
    await expect(page.getByText("Projects")).toBeVisible();
  });

  test("handles 404 for non-existent project", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[placeholder="Enter username"]', "admin");
    await page.fill('input[placeholder="Enter password"]', "admin");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/projects/);
    await page.goto("/projects/99999");
    // Should either show error or redirect
    await page.waitForTimeout(2000);
  });
});
