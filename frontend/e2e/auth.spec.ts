import { test, expect } from "@playwright/test";

const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME ?? "admin";
const ADMIN_PASSWORD =
  process.env.E2E_ADMIN_PASSWORD ?? "CHANGE_ME_to_a_secure_admin_password";

test.describe("Authentication", () => {
  test("should show login page when not authenticated", async ({ page }) => {
    await page.goto("/projects", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("should login with valid credentials", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Enter username").fill(ADMIN_USERNAME);
    await page.getByPlaceholder("Enter password").fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    // Should redirect to projects page after login
    await expect(page).toHaveURL(/\/projects/);
  });

  test("should show error with invalid credentials", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Enter username").fill(ADMIN_USERNAME);
    await page.getByPlaceholder("Enter password").fill("wrong_password");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Invalid username or password")).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test("should preserve redirect URL after login", async ({ page }) => {
    // Try to access a protected page
    await page.goto("/projects", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(/\/login\?redirect/);

    // Login
    await page.getByPlaceholder("Enter username").fill(ADMIN_USERNAME);
    await page.getByPlaceholder("Enter password").fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();

    // Should redirect back to the original page
    await expect(page).toHaveURL(/\/projects/);
  });

  test("should redirect authenticated user from login to home", async ({
    page,
  }) => {
    // Login first
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Enter username").fill(ADMIN_USERNAME);
    await page.getByPlaceholder("Enter password").fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/projects/);

    // Try to go to login page again
    await page.goto("/login", { waitUntil: "domcontentloaded" });
    // Should be redirected away from login
    await expect(page).not.toHaveURL(/\/login/);
  });
});
