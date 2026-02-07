import { test, expect, type Page } from "@playwright/test";
import path from "path";
import fs from "fs";

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

async function createProjectWithData(
  page: Page,
  name: string,
): Promise<string> {
  // Create project
  await page.getByRole("button", { name: "New Project" }).click();
  await page.getByPlaceholder("My Project").fill(name);
  await page.getByPlaceholder("user_id").fill("userId");
  await page.getByPlaceholder("item_id").fill("movieId");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByText(name)).toBeVisible({ timeout: 10000 });
  await page.getByText(name).click();
  await expect(page).toHaveURL(/\/projects\/(\d+)/);

  const url = page.url();
  const match = url.match(/\/projects\/(\d+)/);
  const projectId = match?.[1] ?? "";

  // Upload test data
  await page.getByRole("link", { name: /Data/ }).click();
  await page.getByRole("button", { name: "Upload Data" }).click();

  const csvPath = createTestCsv();
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(csvPath);
  await page.getByRole("button", { name: /Upload/ }).click();

  await expect(page).toHaveURL(
    new RegExp(`/projects/${projectId}/data$`),
    { timeout: 30000 },
  );
  await expect(page.getByText("test_data.csv")).toBeVisible({
    timeout: 10000,
  });

  return projectId;
}

function createTestCsv(): string {
  const tmpDir = path.join(__dirname, "..", "playwright-report");
  fs.mkdirSync(tmpDir, { recursive: true });
  const csvPath = path.join(tmpDir, "tuning_test_data.csv");

  // Create a larger dataset for tuning tests
  const rows = ["userId,movieId,rating"];
  for (let user = 1; user <= 20; user++) {
    for (let item = 1; item <= 10; item++) {
      if (Math.random() > 0.3) {
        rows.push(`${user},${item},${Math.floor(Math.random() * 5) + 1}`);
      }
    }
  }
  fs.writeFileSync(csvPath, rows.join("\n"));
  return csvPath;
}

test.describe("Tuning Wizard", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should navigate to tuning list page", async ({ page }) => {
    const name = `E2E Tuning Nav ${Date.now()}`;
    const projectId = await createProjectWithData(page, name);

    // Navigate to tuning page
    await page.getByRole("link", { name: /Tuning/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/tuning`),
    );
    await expect(page.getByText("Tuning Jobs")).toBeVisible();
  });

  test("should open tuning wizard with stepper", async ({ page }) => {
    const name = `E2E Tuning Wizard ${Date.now()}`;
    const projectId = await createProjectWithData(page, name);

    // Navigate to new tuning job
    await page.getByRole("link", { name: /Tuning/ }).click();
    await page.getByRole("button", { name: /New.*Job|Start.*Tuning/ }).click();

    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/tuning/new`),
    );
    await expect(page.getByText("New Tuning Job")).toBeVisible();

    // Verify stepper steps are visible
    await expect(page.getByText("Data")).toBeVisible();
    await expect(page.getByText("Split")).toBeVisible();
    await expect(page.getByText("Evaluation")).toBeVisible();
    await expect(page.getByText("Run")).toBeVisible();
  });

  test("should navigate through wizard steps", async ({ page }) => {
    const name = `E2E Wizard Steps ${Date.now()}`;
    await createProjectWithData(page, name);

    // Go to tuning wizard
    await page.getByRole("link", { name: /Tuning/ }).click();
    await page.getByRole("button", { name: /New.*Job|Start.*Tuning/ }).click();
    await expect(page.getByText("New Tuning Job")).toBeVisible();

    // Step 1: Select Training Data
    await expect(page.getByText("Select Training Data")).toBeVisible();

    // Select the uploaded data from dropdown
    const dataSelect = page.locator(".p-select").first();
    await dataSelect.click();
    await page.locator(".p-select-option").first().click();

    // Click Next
    await page.getByRole("button", { name: "Next" }).click();

    // Step 2: Split Configuration
    await expect(page.getByText("Split Configuration")).toBeVisible();
    await expect(page.getByText("Scheme")).toBeVisible();
    await expect(page.getByText("Heldout Ratio")).toBeVisible();
    await expect(page.getByText("Test User Ratio")).toBeVisible();

    // Click Next
    await page.getByRole("button", { name: "Next" }).click();

    // Step 3: Evaluation Configuration
    await expect(page.getByText("Evaluation Configuration")).toBeVisible();
    await expect(page.getByText("Target Metric")).toBeVisible();
    await expect(page.getByText("Cutoff")).toBeVisible();

    // Click Next
    await page.getByRole("button", { name: "Next" }).click();

    // Step 4: Job Configuration
    await expect(page.getByText("Job Configuration")).toBeVisible();
    await expect(page.getByText("Number of Trials")).toBeVisible();
    await expect(page.getByText("Parallel Tasks")).toBeVisible();
    await expect(page.getByText("Memory Budget")).toBeVisible();
    await expect(
      page.getByText("Train model after tuning completes"),
    ).toBeVisible();
  });

  test("should go back through wizard steps", async ({ page }) => {
    const name = `E2E Wizard Back ${Date.now()}`;
    await createProjectWithData(page, name);

    // Go to tuning wizard
    await page.getByRole("link", { name: /Tuning/ }).click();
    await page.getByRole("button", { name: /New.*Job|Start.*Tuning/ }).click();

    // Select data and go to step 2
    const dataSelect = page.locator(".p-select").first();
    await dataSelect.click();
    await page.locator(".p-select-option").first().click();
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText("Split Configuration")).toBeVisible();

    // Go back to step 1
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.getByText("Select Training Data")).toBeVisible();
  });

  test("should submit a tuning job", async ({ page }) => {
    const name = `E2E Submit Tuning ${Date.now()}`;
    const projectId = await createProjectWithData(page, name);

    // Go to tuning wizard
    await page.getByRole("link", { name: /Tuning/ }).click();
    await page.getByRole("button", { name: /New.*Job|Start.*Tuning/ }).click();

    // Step 1: Select data
    const dataSelect = page.locator(".p-select").first();
    await dataSelect.click();
    await page.locator(".p-select-option").first().click();
    await page.getByRole("button", { name: "Next" }).click();

    // Step 2: Split (use defaults)
    await page.getByRole("button", { name: "Next" }).click();

    // Step 3: Evaluation (use defaults)
    await page.getByRole("button", { name: "Next" }).click();

    // Step 4: Submit
    await page.getByRole("button", { name: "Start Tuning" }).click();

    // Should redirect to the tuning job detail page
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/tuning/\\d+`),
      { timeout: 15000 },
    );
  });
});
