import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser-live",
  outputDir: "../tmp/phase8/browser-artifacts",
  workers: 1,
  retries: 0,
  timeout: 90000,
  expect: { timeout: 10000 },
  forbidOnly: !!process.env.CI,
  reporter: [
    ["list"],
    ["json", { outputFile: "../tmp/phase8/browser-results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:4178",
    actionTimeout: 10000,
    navigationTimeout: 15000,
    ...devices["Desktop Chrome"],
    trace: "off",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4178 --strictPort",
    url: "http://127.0.0.1:4178",
    reuseExistingServer: false,
  },
});
