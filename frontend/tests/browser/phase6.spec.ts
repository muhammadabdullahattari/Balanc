import { test, expect, type Page } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
const id = "00000000-0000-4000-8000-000000000001";
const business = {
  id,
  name: "Phase Six Synthetic Books",
  currency: "USD",
  basis: "accrual",
  version: 1,
  currency_precision: 2,
  timezone: "UTC",
  policy_version: 1,
  fiscal_month: 1,
  jurisdiction: "US",
  p2_insights_available: false,
  p2_micro_close_available: false,
  p2_prepaids_available: false,
  p2_projects_available: false,
  p2_reconciliation_available: false,
  p2_audit_available: false,
  p2_retention_available: false,
} satisfies components["schemas"]["BusinessRead"];
const account = {
  id,
  code: "100",
  name: "Synthetic cash",
  kind: "asset",
  control: null,
  active: true,
  version: 1,
} satisfies components["schemas"]["AccountRead"];
const identity = {
  id,
  email: "synthetic@example.invalid",
  mfa: true,
  verified: true,
} satisfies components["schemas"]["IdentityRead"];
async function setup(page: Page, role = "accountant") {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  let signedIn = true;
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    let body: unknown;
    if (path === "/api/v1/auth/me") {
      if (!signedIn)
        return route.fulfill({
          status: 401,
          json: { error: { message: "Signed out" } },
        });
      body = identity;
    } else if (path === "/api/v1/auth/csrf")
      body = { csrf_token: "synthetic-csrf" };
    else if (path === "/api/v1/auth/logout") {
      signedIn = false;
      body = { status: "ok" };
    } else if (path === "/api/v1/businesses")
      body = { items: [business], next_cursor: null };
    else if (path === `/api/v1/businesses/${id}`) body = business;
    else if (path.endsWith("/membership"))
      body = {
        items: [
          {
            user_id: id,
            role,
            active: true,
            can_post: role === "accountant",
            can_invite: false,
            version: 1,
          },
        ],
      };
    else if (path.endsWith("/accounts"))
      body = { items: [account], next_cursor: null };
    else if (path.endsWith("/parties") || path.endsWith("/trade-documents"))
      body = { items: [], next_cursor: null };
    else
      return route.fulfill({
        status: 503,
        json: {
          error: { message: "Synthetic dependency unavailable" },
          request_id: "phase6-synthetic",
        },
      });
    return route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: /Phase Six Synthetic Books/ }).click();
  await expect(
    page.getByRole("heading", { name: "Financial overview" }),
  ).toBeVisible();
  return failures;
}
async function navigate(page: Page, label: string, group?: string) {
  const menu = page.getByRole("button", { name: "Open navigation" });
  if (await menu.isVisible()) await menu.click();
  if (group) {
    const details = page
      .locator("nav details")
      .filter({ has: page.locator("summary", { hasText: group }) });
    if (!((await details.getAttribute("open")) !== null))
      await details.locator("summary").click();
  }
  await page.getByRole("link", { name: label, exact: true }).click();
}

test("every workspace route fits desktop, tablet and phone viewports", async ({
  page,
}) => {
  test.setTimeout(120000);
  const failures = await setup(page);
  const routes = [
    ["Overview", "Financial overview"],
    ["Banking", "Bank transactions"],
    ["Sales", "Sales invoices"],
    ["Purchases", "Supplier bills"],
    ["Contacts", "Contacts"],
    ["Documents", "Supporting documents"],
    ["Reports", "Financial reports"],
    ["General ledger", "General ledger", "Accounting"],
    ["Accounts", "Chart of accounts", "Accounting"],
    ["Draft review", "Draft review", "Accounting"],
    ["Payments & refunds", "Payments and refunds", "Accounting"],
    ["Advances", "Advances", "Accounting"],
    ["Loans", "Loans", "Accounting"],
    ["Reconciliation", "Bank reconciliation", "Accounting"],
    ["Month-end close", "Month-end close", "Accounting"],
    ["Bank rules", "Bank rules", "Manage"],
    ["Opening balances", "Opening balances", "Manage"],
    ["Activity", "Activity and notifications", "Manage"],
    ["Search", "Search records", "Manage"],
    ["Settings", "Workspace settings"],
  ];
  for (const width of [1600, 1280, 820, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [label, heading, group] of routes) {
      await navigate(page, label, group);
      await expect(
        page.getByRole("heading", { name: heading, level: 1, exact: true }),
      ).toBeVisible();
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(width);
    }
  }
  expect(failures).toEqual([]);
});

test("mobile navigation restores focus and report tabs support keyboard selection", async ({
  page,
}) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const menu = page.getByRole("button", { name: "Open navigation" });
  await menu.click();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await navigate(page, "Reports");
  const profit = page.getByRole("tab", { name: "Profit & loss" });
  const balance = page.getByRole("tab", { name: "Balance sheet" });
  await profit.focus();
  await page.keyboard.press("ArrowRight");
  await expect(balance).toBeFocused();
  await expect(balance).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("tabpanel", { name: "Balance sheet" }),
  ).toBeVisible();
});
test("account form uses native validation, classifies and submits only after complete input", async ({
  page,
}) => {
  const failures = await setup(page);
  await navigate(page, "Accounts", "Accounting");
  await expect(
    page.getByRole("cell", { name: "Synthetic cash", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Next page/ })).toBeDisabled();
  let writes = 0;
  await page.route("**/accounts", async (route) => {
    if (route.request().method() === "POST") {
      writes++;
      expect(route.request().postDataJSON()).toEqual({
        code: "600",
        name: "Test expense",
        kind: "expense",
        control: null,
      });
      return route.fulfill({ json: account });
    }
    return route.fulfill({ json: { items: [account], next_cursor: null } });
  });
  await page.getByRole("button", { name: "New account" }).click();
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  expect(writes).toBe(0);
  await page.getByLabel("Account code").fill("600");
  await page.getByLabel("Account name").fill("Test expense");
  await page.getByLabel("Classification").selectOption("expense");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeHidden();
  expect(writes).toBe(1);
  expect(failures).toEqual([]);
});
test("sales and purchases filters reach the API with the selected document type", async ({
  page,
}) => {
  const failures = await setup(page);
  for (const [label, value] of [
    ["Sales", "customer_credit"],
    ["Purchases", "vendor_credit"],
  ]) {
    await navigate(page, label);
    const request = page.waitForRequest(
      (r) =>
        r.url().includes("/trade-documents?") &&
        new URL(r.url()).searchParams.get("kind") === value,
    );
    await page.getByLabel("Document type filter").selectOption(value);
    await request;
    await expect(page.getByLabel("Document type filter")).toHaveValue(value);
  }
  expect(failures).toEqual([]);
});
test("read errors show references and recover only on explicit retry", async ({
  page,
}) => {
  await setup(page);
  let reads = 0;
  await page.route("**/accounts?*", async (route) => {
    reads++;
    return reads === 1
      ? route.fulfill({
          status: 503,
          json: {
            error: { message: "Service unavailable" },
            request_id: "phase6-ref",
          },
        })
      : route.fulfill({ json: { items: [account], next_cursor: null } });
  });
  await navigate(page, "Accounts", "Accounting");
  await expect(page.getByRole("alert")).toContainText("phase6-ref");
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("cell", { name: "Synthetic cash", exact: true }),
  ).toBeVisible();
  expect(reads).toBe(2);
});
test("expired session returns to sign-in without a refresh loop", async ({
  page,
}) => {
  let refreshes = 0;
  await page.route("**/api/v1/**", (route) => {
    if (route.request().url().endsWith("/google/config"))
      return route.fulfill({
        json: { enabled: false, client_id: null, local_mailbox: null },
      });
    if (route.request().url().endsWith("/csrf"))
      return route.fulfill({ json: { csrf_token: "test" } });
    if (route.request().url().endsWith("/refresh")) refreshes++;
    return route.fulfill({
      status: 401,
      json: { error: { message: "Session expired" } },
    });
  });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  expect(refreshes).toBe(1);
});
test("permission rejection remains actionable without presenting success", async ({
  page,
}) => {
  await setup(page, "viewer");
  await page.route("**/accounts?*", (route) =>
    route.fulfill({
      status: 403,
      json: {
        error: { message: "Permission denied" },
        request_id: "forbidden-reference",
      },
    }),
  );
  await navigate(page, "Accounts", "Accounting");
  await expect(page.getByRole("alert")).toContainText("Permission denied");
  await expect(page.getByRole("alert")).toContainText("forbidden-reference");
});
test("stale financial mutation is sent once and leaves the form for review", async ({
  page,
}) => {
  await setup(page);
  await navigate(page, "Accounts", "Accounting");
  let posts = 0;
  await page.route("**/accounts", async (route) => {
    posts++;
    return route.fulfill({
      status: 409,
      json: {
        error: { message: "Record changed; review again" },
        request_id: "conflict-reference",
      },
    });
  });
  await page.getByRole("button", { name: "New account" }).click();
  await page.getByLabel("Account code").fill("610");
  await page.getByLabel("Account name").fill("Synthetic expense");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Record changed");
  expect(posts).toBe(1);
  await expect(page.getByRole("dialog")).toBeVisible();
});
test("aborted reads show connection guidance and allow recovery", async ({
  page,
}) => {
  await setup(page);
  let failed = true;
  await page.route("**/accounts?*", (route) =>
    failed
      ? route.abort("connectionfailed")
      : route.fulfill({ json: { items: [account], next_cursor: null } }),
  );
  await navigate(page, "Accounts", "Accounting");
  await expect(page.getByRole("alert")).toContainText(
    "couldn't reach the server",
  );
  failed = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("cell", { name: "Synthetic cash", exact: true }),
  ).toBeVisible();
});
test("pending read shows loading until the response and never fabricates rows", async ({
  page,
}) => {
  await setup(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/accounts?*", async (route) => {
    await gate;
    return route.fulfill({ json: { items: [], next_cursor: null } });
  });
  await navigate(page, "Accounts", "Accounting");
  await expect(page.getByRole("status")).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "Synthetic cash", exact: true }),
  ).toHaveCount(0);
  release();
  await expect(
    page.getByText("No accounts yet"),
  ).toBeVisible();
});
test("read timeout is bounded and presents recovery without replay", async ({
  page,
}) => {
  test.setTimeout(45000);
  await setup(page);
  let reads = 0;
  await page.route("**/accounts?*", async (route) => {
    reads++;
    await new Promise((resolve) => setTimeout(resolve, 35000));
    await route.abort().catch(() => {});
  });
  await navigate(page, "Accounts", "Accounting");
  await expect(page.getByRole("alert")).toContainText("timed out", {
    timeout: 33000,
  });
  expect(reads).toBe(1);
});
test("dashboard preserves exact totals and exposes chart data and freshness", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/dashboard?*", (route) =>
    route.fulfill({
      json: {
        currency: "USD",
        cash_balance: "123.45",
        receivables: { total: "10" },
        payables: { total: "20" },
        net_profit: "30",
        as_of: "2026-11-02",
        timezone: "UTC",
        basis: "accrual",
        report_id: "phase6-report",
        trend: [
          { start_date: "2026-11-01", income: "100.25", expense: "70.25" },
        ],
      },
    }),
  );
  await page.getByLabel("Cash account").selectOption(id);
  await expect(page.getByText("USD 123.45", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: /Monthly income/ })).toBeVisible();
  await page.getByText("View exact figures").click();
  await expect(page.getByRole("cell", { name: "USD 100.25" })).toBeVisible();
  await expect(page.getByText(/phase6-report/)).toContainText("accrual basis");
});
