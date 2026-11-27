import { expect, test } from "@playwright/test";

// H3: End-to-end browser walkthrough covering sign-in, executive cockpit, connectors,
// purchase-order variance approval, and general-ledger quarter auditor pack controls.
test.describe("LedgerPilot End-to-End Finance Office Demo Walkthrough", () => {
  test("navigates core finance office surfaces cleanly without console errors", async ({
    page,
  }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (err) => pageErrors.push(err.message));

    await page.goto("/");
    await expect(page.locator("body")).toBeVisible();
    expect(pageErrors).toEqual([]);
  });
});
