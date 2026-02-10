import { test, expect } from "@playwright/test";

test("API is reachable", async ({ request }) => {
  // Poll until the backend is ready (containers may still be starting)
  const maxAttempts = 30;
  const interval = 2000;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const res = await request.get("/api/ping/");
      if (res.ok()) return;
    } catch {
      // connection refused — backend not up yet
    }
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("Backend did not become ready within 60 seconds");
});

test("login page renders", async ({ page }) => {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});
