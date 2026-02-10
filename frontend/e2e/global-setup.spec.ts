import { test, expect } from "@playwright/test";

test("API is reachable", async ({ request }) => {
  // CI workflow already waits for backend readiness via curl.
  // This is a quick smoke check with a few retries for safety.
  const maxAttempts = 5;
  let lastStatus = 0;
  for (let i = 0; i < maxAttempts; i++) {
    const res = await request.get("/api/ping/");
    lastStatus = res.status();
    if (res.ok()) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(
    `Backend returned HTTP ${lastStatus} after ${maxAttempts} attempts`,
  );
});

test("login page renders", async ({ page }) => {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
