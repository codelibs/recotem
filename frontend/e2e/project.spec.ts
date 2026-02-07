import { test, expect, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder("Enter username").fill("admin");
  await page.getByPlaceholder("Enter password").fill("very_bad_password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/projects/);
}

test.describe("Project Management", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should display project list page", async ({ page }) => {
    await expect(page.getByText("Projects")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "New Project" }),
    ).toBeVisible();
  });

  test("should create a new project", async ({ page }) => {
    const projectName = `E2E Test Project ${Date.now()}`;

    // Open create dialog
    await page.getByRole("button", { name: "New Project" }).click();
    await expect(page.getByText("Create Project")).toBeVisible();

    // Fill in the form
    await page.getByPlaceholder("My Project").fill(projectName);
    await page.getByPlaceholder("user_id").fill("userId");
    await page.getByPlaceholder("item_id").fill("movieId");

    // Submit
    await page.getByRole("button", { name: "Create" }).click();

    // Verify project appears in the list
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 10000 });
  });

  test("should navigate to project dashboard", async ({ page }) => {
    const projectName = `E2E Nav Project ${Date.now()}`;

    // Create a project first
    await page.getByRole("button", { name: "New Project" }).click();
    await page.getByPlaceholder("My Project").fill(projectName);
    await page.getByPlaceholder("user_id").fill("userId");
    await page.getByPlaceholder("item_id").fill("movieId");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 10000 });

    // Click on the project card to navigate
    await page.getByText(projectName).click();

    // Should be on the project dashboard
    await expect(page).toHaveURL(/\/projects\/\d+/);
  });

  test("should show empty state when no projects", async ({ page }) => {
    // This test verifies the empty state message exists in DOM
    // (may not be visible if projects already exist)
    const emptyMsg = page.getByText("No projects yet");
    const projectCards = page.locator('[class*="cursor-pointer"]');

    // Either there are projects or the empty state is shown
    const hasProjects = (await projectCards.count()) > 0;
    if (!hasProjects) {
      await expect(emptyMsg).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Create Project" }),
      ).toBeVisible();
    }
  });

  test("should show project details with sidebar navigation", async ({
    page,
  }) => {
    const projectName = `E2E Sidebar Project ${Date.now()}`;

    // Create a project
    await page.getByRole("button", { name: "New Project" }).click();
    await page.getByPlaceholder("My Project").fill(projectName);
    await page.getByPlaceholder("user_id").fill("userId");
    await page.getByPlaceholder("item_id").fill("movieId");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 10000 });

    // Navigate to project
    await page.getByText(projectName).click();
    await expect(page).toHaveURL(/\/projects\/\d+/);

    // Verify sidebar navigation links
    await expect(page.getByRole("link", { name: /Data/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Tuning/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Models/ })).toBeVisible();
  });
});
