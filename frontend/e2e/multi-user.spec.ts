import { test, expect } from "@playwright/test";

const USER_A_USERNAME = process.env.E2E_USER_A_USERNAME ?? "e2e_user_a";
const USER_A_PASSWORD = process.env.E2E_USER_A_PASSWORD ?? "e2e_password_a";
const USER_B_USERNAME = process.env.E2E_USER_B_USERNAME ?? "e2e_user_b";
const USER_B_PASSWORD = process.env.E2E_USER_B_PASSWORD ?? "e2e_password_b";
const API_BASE_URL = process.env.E2E_API_BASE_URL ?? "http://localhost:8000/api/v1";

type AuthResponse = {
  access: string;
  refresh?: string;
};

async function postJson(
  url: string,
  payload: unknown,
  headers: Record<string, string> = {},
) {
  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...headers,
    },
    body: JSON.stringify(payload),
  });
}

async function loginViaApi(username: string, password: string): Promise<AuthResponse> {
  const response = await postJson(`${API_BASE_URL}/auth/login/`, {
    username,
    password,
  });
  if (!response.ok) {
    throw new Error(
      `Failed to login user '${username}': ${response.status} ${await response.text()}`,
    );
  }
  return (await response.json()) as AuthResponse;
}

async function createProjectViaApi(
  token: string,
  name: string,
) {
  const response = await postJson(
    `${API_BASE_URL}/project/`,
    {
      name,
      user_column: "userId",
      item_column: "itemId",
      time_column: null,
    },
    {
      Authorization: `Bearer ${token}`,
    },
  );
  if (!response.ok) {
    throw new Error(
      `Failed to create project '${name}': ${response.status} ${await response.text()}`,
    );
  }
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
      const authA = await loginViaApi(USER_A_USERNAME, USER_A_PASSWORD);
      const authB = await loginViaApi(USER_B_USERNAME, USER_B_PASSWORD);

      await createProjectViaApi(authA.access, userAProject);
      await createProjectViaApi(authB.access, userBProject);

      await contextA.addInitScript(
        ({ access, refresh }) => {
          sessionStorage.setItem("access_token", access);
          sessionStorage.setItem("refresh_token", refresh);
          localStorage.removeItem("lastProjectId");
        },
        { access: authA.access, refresh: authA.refresh ?? "" },
      );
      await pageA.goto("/projects", { waitUntil: "domcontentloaded" });
      await expect(pageA.getByText(userAProject)).toBeVisible();
      await expect(pageA.getByText(userBProject)).toHaveCount(0);

      await contextB.addInitScript(
        ({ access, refresh }) => {
          sessionStorage.setItem("access_token", access);
          sessionStorage.setItem("refresh_token", refresh);
          localStorage.removeItem("lastProjectId");
        },
        { access: authB.access, refresh: authB.refresh ?? "" },
      );
      await pageB.goto("/projects", { waitUntil: "domcontentloaded" });
      await expect(pageB.getByText(userAProject)).toHaveCount(0);
      await expect(pageB.getByText(userBProject)).toBeVisible();
    } finally {
      await contextA.close();
      await contextB.close();
    }
  });
});
