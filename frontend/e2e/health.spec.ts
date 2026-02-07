import { test, expect } from "@playwright/test";

test("API health check", async ({ request }) => {
  const response = await request.get("/api/ping/");
  expect(response.ok()).toBeTruthy();
});
