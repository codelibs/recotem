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

async function createProject(page: Page, name: string): Promise<string> {
  await page.getByRole("button", { name: "New Project" }).click();
  await page.getByPlaceholder("My Project").fill(name);
  await page.getByPlaceholder("user_id").fill("userId");
  await page.getByPlaceholder("item_id").fill("movieId");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.getByText(name)).toBeVisible({ timeout: 10000 });
  await page.getByText(name).click();
  await expect(page).toHaveURL(/\/projects\/(\d+)/);

  const url = page.url();
  const match = url.match(/\/projects\/(\d+)/);
  return match?.[1] ?? "";
}

test.describe("Model Management", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should navigate to models list page", async ({ page }) => {
    const name = `E2E Model Nav ${Date.now()}`;
    const projectId = await createProject(page, name);

    // Navigate to models page
    await page.locator("nav").getByRole("link", { name: /Models/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/models`),
    );
    await expect(page.getByText("Trained Models")).toBeVisible();
  });

  test("should show empty models table", async ({ page }) => {
    const name = `E2E Model Empty ${Date.now()}`;
    await createProject(page, name);

    // Navigate to models page
    await page.locator("nav").getByRole("link", { name: /Models/ }).click();
    await expect(page.getByText("Trained Models")).toBeVisible();

    // Should show empty message
    await expect(page.getByText("No trained models yet")).toBeVisible();
  });

  test("should show Train Model button", async ({ page }) => {
    const name = `E2E Model Btn ${Date.now()}`;
    await createProject(page, name);

    await page.locator("nav").getByRole("link", { name: /Models/ }).click();
    await expect(
      page.getByRole("button", { name: "Train Model" }),
    ).toBeVisible();
  });

  test("should navigate to train model page", async ({ page }) => {
    const name = `E2E Model Train ${Date.now()}`;
    const projectId = await createProject(page, name);

    await page.locator("nav").getByRole("link", { name: /Models/ }).click();
    await page.getByRole("button", { name: "Train Model" }).click();

    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/models/train`),
    );
  });

  test("should show empty state or model table", async ({ page }) => {
    const name = `E2E Model Cols ${Date.now()}`;
    await createProject(page, name);

    await page.locator("nav").getByRole("link", { name: /Models/ }).click();
    await expect(page.getByText("Trained Models")).toBeVisible();

    // New project has no models — verify empty state is shown
    await expect(page.getByText("No trained models yet")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Train Model" }),
    ).toBeVisible();
  });
});
