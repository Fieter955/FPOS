import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || "msedge",
    headless: true,
    viewport: { width: 1440, height: 1200 },
  },
  webServer: {
    command: "python -m http.server 4173 --directory frontend",
    url: "http://127.0.0.1:4173/inventory.html",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
