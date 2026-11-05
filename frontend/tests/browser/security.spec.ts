import { test, expect } from "@playwright/test";

test("production CSP blocks injected inline scripts and keeps the app usable", async ({
  page,
}) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith("/google/config")
      ? { enabled: false, client_id: null, local_mailbox: null }
      : path.endsWith("/csrf")
        ? { csrf_token: "synthetic-security-check-token" }
        : { error: { code: "ERR_AUTH", message: "Sign in to continue." } };
    await route.fulfill({
      status: path.endsWith("/me") ? 401 : 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  const response = await page.goto("/");
  expect(response?.headers()["x-frame-options"]).toBe("DENY");
  await expect(page.locator("#root")).not.toBeEmpty();
  const blocked = await page.evaluate(async () => {
    const violation = new Promise<string>((resolve) =>
      document.addEventListener(
        "securitypolicyviolation",
        (event) => resolve(event.effectiveDirective),
        { once: true },
      ),
    );
    const injected = document.createElement("script");
    injected.textContent =
      "document.documentElement.dataset.securityInjected = 'executed'";
    document.head.appendChild(injected);
    return {
      directive: await violation,
      executed: document.documentElement.dataset.securityInjected,
    };
  });
  expect(blocked.directive).toBe("script-src-elem");
  expect(blocked.executed).toBeUndefined();
});
