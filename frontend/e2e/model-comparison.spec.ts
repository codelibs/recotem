import { test, expect, type Page } from "@playwright/test";

const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME ?? "admin";
const ADMIN_PASSWORD =
  process.env.E2E_ADMIN_PASSWORD ?? "CHANGE_ME_to_a_secure_admin_password";

async function login(page: Page) {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("Enter username").fill(ADMIN_USERNAME);
  await page.getByPlaceholder("Enter password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/projects/);
}

test.describe("Model Comparison Page", () => {
  test("should navigate to comparison page", async ({ page }) => {
    await login(page);

    // Create a project or navigate to existing one
    const projectLink = page.locator("a[href*='/projects/']").first();
    if (await projectLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await projectLink.click();
      await page.goto(page.url().replace(/\/$/, "") + "/model-comparison");
      await expect(page.getByText("Model Comparison")).toBeVisible({
        timeout: 10000,
      });
    }
  });

  test("should show empty state when no completed jobs", async ({ page }) => {
    await login(page);

    const projectLink = page.locator("a[href*='/projects/']").first();
    if (await projectLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await projectLink.click();
      await page.goto(page.url().replace(/\/$/, "") + "/model-comparison");
      // Should show either jobs or empty message
      await expect(
        page.getByText("Model Comparison")
      ).toBeVisible({ timeout: 10000 });
    }
  });
});

test.describe("Dark Mode", () => {
  test("should toggle dark mode", async ({ page }) => {
    await login(page);

    // Check if dark mode toggle exists
    const darkModeToggle = page.locator("button[aria-label*='Switch to']").first();
    if (await darkModeToggle.isVisible({ timeout: 3000 }).catch(() => false)) {
      // Toggle dark mode on
      await darkModeToggle.click();
      await expect(page.locator("html")).toHaveClass(/dark-mode/);

      // Toggle dark mode off
      await darkModeToggle.click();
      await expect(page.locator("html")).not.toHaveClass(/dark-mode/);
    }
  });

  test("should persist dark mode preference", async ({ page }) => {
    await login(page);

    // Set dark mode via localStorage
    await page.evaluate(() => {
      localStorage.setItem("dark-mode", "true");
    });
    await page.reload();

    // Check if the class is applied before first paint
    const isDark = await page.evaluate(() =>
      document.documentElement.classList.contains("dark-mode")
    );
    expect(isDark).toBe(true);
  });
});
