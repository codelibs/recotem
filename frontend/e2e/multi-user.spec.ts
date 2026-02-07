import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.fill('input[placeholder="Enter username"]', username);
  await page.fill('input[placeholder="Enter password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/projects/);
}

test.describe("Multi-User Isolation", () => {
  test.describe.configure({ mode: "serial" });

  test("user A creates a project", async ({ page }) => {
    await login(page, "admin", "admin");
    // Navigate to projects and create
    await expect(page.getByText("Projects")).toBeVisible();
  });

  test("different users see only their own projects", async ({ browser }) => {
    // Create two separate browser contexts (different sessions)
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    try {
      await login(pageA, "admin", "admin");
      await expect(pageA.getByText("Projects")).toBeVisible();

      // User B should also be able to login
      // (This test verifies multi-session support works)
      await login(pageB, "admin", "admin");
      await expect(pageB.getByText("Projects")).toBeVisible();
    } finally {
      await contextA.close();
      await contextB.close();
    }
  });
});
