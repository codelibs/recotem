import { test, expect } from "@playwright/test";

test.describe("Authentication", () => {
  test("should show login page when not authenticated", async ({ page }) => {
    await page.goto("/projects");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByText("Sign in")).toBeVisible();
  });

  test("should login with valid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.getByPlaceholder("Enter username").fill("admin");
    await page.getByPlaceholder("Enter password").fill("very_bad_password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // Should redirect to projects page after login
    await expect(page).toHaveURL(/\/projects/);
  });

  test("should show error with invalid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.getByPlaceholder("Enter username").fill("admin");
    await page.getByPlaceholder("Enter password").fill("wrong_password");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Invalid username or password")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("should preserve redirect URL after login", async ({ page }) => {
    // Try to access a protected page
    await page.goto("/projects");
    await expect(page).toHaveURL(/\/login\?redirect/);

    // Login
    await page.getByPlaceholder("Enter username").fill("admin");
    await page.getByPlaceholder("Enter password").fill("very_bad_password");
    await page.getByRole("button", { name: "Sign in" }).click();

    // Should redirect back to the original page
    await expect(page).toHaveURL(/\/projects/);
  });

  test("should redirect authenticated user from login to home", async ({
    page,
  }) => {
    // Login first
    await page.goto("/login");
    await page.getByPlaceholder("Enter username").fill("admin");
    await page.getByPlaceholder("Enter password").fill("very_bad_password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/projects/);

    // Try to go to login page again
    await page.goto("/login");
    // Should be redirected away from login
    await expect(page).not.toHaveURL(/\/login/);
  });
});
