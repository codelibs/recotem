import { test, expect, type Page } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

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

  // Navigate to the project
  await page.getByText(name).click();
  await expect(page).toHaveURL(/\/projects\/(\d+)/);

  // Extract project ID from URL
  const url = page.url();
  const match = url.match(/\/projects\/(\d+)/);
  return match?.[1] ?? "";
}

// Create a temporary CSV test data file
function createTestCsv(): string {
  const tmpDir = path.join(__dirname, "..", "playwright-report");
  fs.mkdirSync(tmpDir, { recursive: true });
  const csvPath = path.join(tmpDir, "test_data.csv");
  const csvContent = [
    "userId,movieId,rating",
    "1,101,5",
    "1,102,3",
    "2,101,4",
    "2,103,5",
    "3,102,2",
    "3,103,4",
  ].join("\n");
  fs.writeFileSync(csvPath, csvContent);
  return csvPath;
}

test.describe("Data Management", () => {
  let projectId: string;

  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("should navigate to data list page", async ({ page }) => {
    const name = `E2E Data Project ${Date.now()}`;
    projectId = await createProject(page, name);

    // Navigate to data page
    await page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data`),
    );
    await expect(page.getByText("Training Data")).toBeVisible();
  });

  test("should navigate to upload page", async ({ page }) => {
    const name = `E2E Upload Nav ${Date.now()}`;
    projectId = await createProject(page, name);

    // Go to data page
    await page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ }).click();
    await expect(page.getByText("Training Data")).toBeVisible();

    // Click upload button
    await page.getByRole("button", { name: "Upload Data" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data/upload`),
    );
    await expect(page.getByText("Upload Training Data")).toBeVisible();
  });

  test("should upload a CSV file", async ({ page }) => {
    const name = `E2E Upload Project ${Date.now()}`;
    projectId = await createProject(page, name);

    // Navigate to upload page
    await page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ }).click();
    await page.getByRole("button", { name: "Upload Data" }).click();
    await expect(page.getByText("Upload Training Data")).toBeVisible();

    // Create test CSV
    const csvPath = createTestCsv();

    // Upload the file using the file chooser
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(csvPath);

    // Click the upload button in the FileUpload component
    await page.getByRole("button", { name: /Upload/ }).click();

    // Should redirect to data list
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data$`),
      { timeout: 30000 },
    );

    // The uploaded file should appear in the list
    await expect(page.getByText("test_data.csv")).toBeVisible({
      timeout: 10000,
    });
  });

  test("should show data list with uploaded files", async ({ page }) => {
    const name = `E2E DataList ${Date.now()}`;
    projectId = await createProject(page, name);

    // Upload a file first
    await page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ }).click();
    await page.getByRole("button", { name: "Upload Data" }).click();
    const csvPath = createTestCsv();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(csvPath);
    await page.getByRole("button", { name: /Upload/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data$`),
      { timeout: 30000 },
    );

    // Verify table columns
    await expect(page.getByText("File Name")).toBeVisible();
    await expect(page.getByText("Size")).toBeVisible();
    await expect(page.getByText("Uploaded", { exact: true })).toBeVisible();
    await expect(page.getByText("Actions")).toBeVisible();
  });

  test("should navigate to data detail page", async ({ page }) => {
    const name = `E2E DataDetail ${Date.now()}`;
    projectId = await createProject(page, name);

    // Upload a file
    await page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ }).click();
    await page.getByRole("button", { name: "Upload Data" }).click();
    const csvPath = createTestCsv();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(csvPath);
    await page.getByRole("button", { name: /Upload/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data$`),
      { timeout: 30000 },
    );

    // Click the data link to see details
    await page.getByRole("link", { name: "test_data.csv" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data/\\d+`),
    );
  });

  test("should delete uploaded data", async ({ page }) => {
    const name = `E2E DataDelete ${Date.now()}`;
    projectId = await createProject(page, name);

    // Upload a file
    await page.locator("nav[aria-label='Sidebar']").getByRole("link", { name: /Data/ }).click();
    await page.getByRole("button", { name: "Upload Data" }).click();
    const csvPath = createTestCsv();
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(csvPath);
    await page.getByRole("button", { name: /Upload/ }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/data$`),
      { timeout: 30000 },
    );

    // Confirm the data is present
    await expect(page.getByText("test_data.csv")).toBeVisible();

    // Click delete button (trash icon)
    await page.getByRole("button", { name: /Delete/ }).first().click();

    // Confirm in the ConfirmDialog
    await page.getByRole("alertdialog").getByRole("button", { name: "Delete" }).click();

    // Data should be removed
    await expect(page.getByRole("link", { name: "test_data.csv" })).not.toBeVisible({
      timeout: 10000,
    });
  });
});
