import { test, expect } from "@playwright/test";

test("API is reachable", async ({ request }) => {
  const res = await request.get("/api/ping/");
  expect(res.ok()).toBeTruthy();
});

test("login page renders", async ({ page }) => {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
