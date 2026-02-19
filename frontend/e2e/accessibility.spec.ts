import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const USERNAME = process.env.E2E_USER_A_USERNAME || "e2e_user_a";
const PASSWORD = process.env.E2E_USER_A_PASSWORD || "e2e_password_a";

async function login(page: Page) {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("Enter username").fill(USERNAME);
  await page.getByPlaceholder("Enter password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/projects/);
}

async function getProjectId(page: Page): Promise<string | null> {
  // Navigate to projects page and grab the first project link
  const projectLink = page.locator("a[href*='/projects/']").first();
  if (await projectLink.isVisible({ timeout: 5000 }).catch(() => false)) {
    const href = await projectLink.getAttribute("href");
    const match = href?.match(/\/projects\/(\d+)/);
    return match ? match[1] : null;
  }
  // Fallback: try to extract from any visible project card
  const projectCard = page.locator("[class*='cursor-pointer']").first();
  if (await projectCard.isVisible({ timeout: 3000 }).catch(() => false)) {
    await projectCard.click();
    const url = page.url();
    const match = url.match(/\/projects\/(\d+)/);
    return match ? match[1] : null;
  }
  return null;
}

test.describe("Accessibility", () => {
  test("Login page has no critical a11y violations", async ({ page }) => {
    await page.goto("/login");
    await page.waitForSelector("form");

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .disableRules(["aria-allowed-attr"]) // PrimeVue Password component upstream issue
      .analyze();

    expect(results.violations.filter(v => v.impact === "critical")).toEqual([]);
  });

  test("Projects page has no critical a11y violations", async ({ page }) => {
    await login(page);
    await page.waitForSelector("[role='navigation']");

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();

    expect(results.violations.filter(v => v.impact === "critical")).toEqual([]);
  });

  test("Tuning Job List page has no critical a11y violations", async ({ page }) => {
    await login(page);
    await page.waitForSelector("[role='navigation']");

    const projectId = await getProjectId(page);
    test.skip(!projectId, "No project available for testing");

    await page.goto(`/projects/${projectId}/tuning`);
    // Wait for the page content to load (heading or empty state or table)
    await page.waitForSelector("h2, [class*='empty-state'], table", { timeout: 10000 });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();

    expect(results.violations.filter(v => v.impact === "critical")).toEqual([]);
  });

  test("Model List page has no critical a11y violations", async ({ page }) => {
    await login(page);
    await page.waitForSelector("[role='navigation']");

    const projectId = await getProjectId(page);
    test.skip(!projectId, "No project available for testing");

    await page.goto(`/projects/${projectId}/models`);
    // Wait for the page content to load (heading or empty state or table)
    await page.waitForSelector("h2, [class*='empty-state'], table", { timeout: 10000 });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();

    expect(results.violations.filter(v => v.impact === "critical")).toEqual([]);
  });

  test("Model Comparison page has no critical a11y violations", async ({ page }) => {
    await login(page);
    await page.waitForSelector("[role='navigation']");

    const projectId = await getProjectId(page);
    test.skip(!projectId, "No project available for testing");

    await page.goto(`/projects/${projectId}/model-comparison`);
    // Wait for the page content to load (heading or empty state message)
    await page.waitForSelector("h2, [class*='text-neutral']", { timeout: 10000 });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();

    expect(results.violations.filter(v => v.impact === "critical")).toEqual([]);
  });
});
