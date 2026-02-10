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

test.describe("Project Management", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should display project list page", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "New Project" }),
    ).toBeVisible();
  });

  test("should create a new project", async ({ page }) => {
    const projectName = `E2E Test Project ${Date.now()}`;

    // Open create dialog
    await page.getByRole("button", { name: "New Project" }).click();
    await expect(page.getByRole("dialog").getByText("Create Project")).toBeVisible();

    // Fill in the form
    await page.getByPlaceholder("My Project").fill(projectName);
    await page.getByPlaceholder("user_id").fill("userId");
    await page.getByPlaceholder("item_id").fill("movieId");

    // Submit
    await page.getByRole("button", { name: "Create", exact: true }).click();

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
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 10000 });

    // Click on the project card to navigate
    await page.getByText(projectName).click();

    // Should be on the project dashboard
    await expect(page).toHaveURL(/\/projects\/\d+/);
  });

  test("should show project list or empty state", async ({ page }) => {
    // Other parallel tests may create projects, so either state is valid
    const heading = page.getByRole("heading", { name: "Projects" });
    await expect(heading).toBeVisible();

    // Verify the page loaded successfully with either projects or empty state
    const projectCards = page.locator(".cursor-pointer");
    const emptyState = page.getByText("No projects yet");
    await expect(projectCards.first().or(emptyState)).toBeVisible();
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
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect(page.getByText(projectName)).toBeVisible({ timeout: 10000 });

    // Navigate to project
    await page.getByText(projectName).click();
    await expect(page).toHaveURL(/\/projects\/\d+/);

    // Verify sidebar navigation links
    await expect(page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ })).toBeVisible();
    await expect(page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Tuning/ })).toBeVisible();
    await expect(page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Models/ })).toBeVisible();
  });
});
