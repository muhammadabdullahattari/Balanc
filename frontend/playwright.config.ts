import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 7000 },
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [
    ["list"],
    ["json", { outputFile: "../tmp/phase6/browser-results.json" }],
    ["html", { outputFolder: "../tmp/phase6/browser-report", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:4176",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4176 --strictPort",
    url: "http://127.0.0.1:4176",
    reuseExistingServer: false,
  },
});
