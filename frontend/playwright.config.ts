import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: "http://localhost:8000",
    headless: true,
  },
  webServer: undefined, // Services started separately via docker compose
});
