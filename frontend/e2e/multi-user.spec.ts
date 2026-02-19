import { test, expect, type Page } from "@playwright/test";

const USER_A_USERNAME = process.env.E2E_USER_A_USERNAME ?? "e2e_user_a";
const USER_A_PASSWORD = process.env.E2E_USER_A_PASSWORD ?? "e2e_password_a";
const USER_B_USERNAME = process.env.E2E_USER_B_USERNAME ?? "e2e_user_b";
const USER_B_PASSWORD = process.env.E2E_USER_B_PASSWORD ?? "e2e_password_b";
const API_BASE_URL = process.env.E2E_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function loginInPage(page: Page, username: string, password: string) {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.getByPlaceholder("Enter username").fill(username);
  await page.getByPlaceholder("Enter password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/projects/);
}

test.describe("Multi-User Isolation", () => {
  test("different users see only their own projects", async ({ browser }) => {
    const contextA = await browser.newContext();
    const contextB = await browser.newContext();
    const pageA = await contextA.newPage();
    const pageB = await contextB.newPage();

    const userAProject = `E2E UserA Private ${Date.now()}`;
    const userBProject = `E2E UserB Private ${Date.now()}`;

    try {
      // Login via real browser flow so httpOnly cookies are set
      await loginInPage(pageA, USER_A_USERNAME, USER_A_PASSWORD);
      await loginInPage(pageB, USER_B_USERNAME, USER_B_PASSWORD);

      // Create projects using page.request (shares cookies with browser context)
      await pageA.request.post(`${API_BASE_URL}/project/`, {
        data: {
          name: userAProject,
          user_column: "userId",
          item_column: "itemId",
          time_column: null,
        },
      });
      await pageB.request.post(`${API_BASE_URL}/project/`, {
        data: {
          name: userBProject,
          user_column: "userId",
          item_column: "itemId",
          time_column: null,
        },
      });

      // Reload to see newly created projects
      await pageA.goto("/projects", { waitUntil: "domcontentloaded" });
      await expect(pageA.getByText(userAProject)).toBeVisible();
      await expect(pageA.getByText(userBProject)).toHaveCount(0);

      await pageB.goto("/projects", { waitUntil: "domcontentloaded" });
      await expect(pageB.getByText(userAProject)).toHaveCount(0);
      await expect(pageB.getByText(userBProject)).toBeVisible();
    } finally {
      await contextA.close();
      await contextB.close();
    }
  });
});
