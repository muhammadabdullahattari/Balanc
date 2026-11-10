import fs from "node:fs";
import { test, expect } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
type Fixture = {
  foreign_business: string;
  foreign_account: string;
  foreign_evidence: string;
  email: string;
  password: string;
  recovery: string[];
  party: string;
  accounts: Record<string, string>;
  business: components["schemas"]["BusinessRead"];
};
// Parse exact decimal values into millionths; never compare financial floats.
function micros(value: string) {
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  if (fraction.length > 6) throw Error("Unexpected scale");
  const n = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, "0"));
  return negative ? -n : n;
}
test("real browser posts invoice, receives partial payment, preserves idempotency and tenant boundaries", async ({
  page,
}) => {
  const f: Fixture = JSON.parse(
    fs.readFileSync(process.env.LEDGERPILOT_LIVE_FIXTURE!, "utf8"),
  );
  const base = `/api/v1/businesses/${f.business.id}`,
    day = "2026-10-30";
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("Email address").fill(f.email);
  await page.getByLabel("Password", { exact: true }).fill(f.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Use a recovery code" }).click();
  await page.getByLabel("Recovery code", { exact: true }).fill(f.recovery[0]);
  const choices = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/businesses" && r.ok(),
  );
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  const listed = await (await choices).json();
  const index = listed.items.findIndex(
    (b: { id: string }) => b.id === f.business.id,
  );
  expect(index).toBeGreaterThanOrEqual(0);
  await page
    .getByRole("button", { name: new RegExp(f.business.name) })
    .nth(index)
    .click();
  await expect(
    page.getByRole("heading", { name: "Financial overview" }),
  ).toBeVisible();
  const cookies = await page.context().cookies();
  expect(
    cookies.some(
      (c) => c.name === "__Host-lp_access" && c.httpOnly && c.secure,
    ),
  ).toBe(true);
  await page.getByRole("link", { name: "Sales", exact: true }).click();
  await page.getByRole("button", { name: "New invoice", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Document number").fill("PHASE8-BROWSER-100");
  const customer = dialog.locator('select[name="party_id"]');
  await expect(customer.locator(`option[value="${f.party}"]`)).toHaveCount(1);
  await customer.selectOption(f.party);
  await dialog.getByLabel("Accounting date", { exact: true }).fill(day);
  await dialog.getByLabel("Due date", { exact: true }).fill("2026-11-15");
  await dialog
    .getByLabel("Receivables control account")
    .selectOption(f.accounts.ar);
  await dialog
    .getByLabel("Description", { exact: true })
    .fill("Independent real-browser invoice");
  await dialog
    .getByLabel("Account", { exact: true })
    .selectOption(f.accounts.income);
  await dialog.getByLabel("Amount", { exact: true }).fill("100.00");
  await dialog.getByRole("button", { name: "Review before posting" }).click();
  await expect(
    dialog.getByRole("button", { name: "Approve & post" }),
  ).toBeVisible();
  const empty = await page.request.get(base + "/trade-documents", {
    params: { kind: "invoice", as_of: day },
  });
  expect(empty.ok()).toBe(true);
  expect((await empty.json()).items).toHaveLength(0);
  const posted = page.waitForRequest(
    (r) =>
      r.method() === "POST" &&
      new URL(r.url()).pathname === base + "/trade-documents",
  );
  await dialog.getByRole("button", { name: "Approve & post" }).click();
  const command = await posted;
  await expect(dialog).toBeHidden();
  await expect(
    page.getByText("PHASE8-BROWSER-100", { exact: true }),
  ).toBeVisible();
  const repeated = await page.request.post(base + "/trade-documents", {
    data: command.postDataJSON(),
    headers: {
      "X-CSRF-Token": command.headers()["x-csrf-token"],
      Origin: new URL(page.url()).origin,
    },
  });
  expect(repeated.ok(), await repeated.text()).toBe(true);
  const documents = await (
    await page.request.get(base + "/trade-documents", {
      params: { kind: "invoice", as_of: day },
    })
  ).json();
  expect(documents.items).toHaveLength(1);
  expect(micros(documents.items[0].outstanding)).toBe(100000000n);
  await page.getByRole("button", { name: /View & record/ }).click();
  await dialog.getByLabel("Bank account").selectOption(f.accounts.bank);
  await dialog.getByLabel("Payment date").fill(day);
  // Payment amount label belongs to the settlement form, not the issued document totals.
  await dialog.locator('input[name="amount"]').fill("40.00");
  await dialog.getByRole("button", { name: "Review payment" }).click();
  await expect(
    dialog.getByRole("button", { name: "Approve payment record" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Approve payment record" }).click();
  await expect(dialog).toBeHidden();
  const settled = await (
    await page.request.get(base + "/trade-documents", {
      params: { kind: "invoice", as_of: day },
    })
  ).json();
  expect(micros(settled.items[0].outstanding)).toBe(60000000n);
  const trialReply = await page.request.get(base + "/trial-balance", {
    params: { as_of: day },
  });
  expect(trialReply.ok()).toBe(true);
  const trial = await trialReply.json();
  expect(trial.balanced).toBe(true);
  const amounts = new Map<string, bigint>(
    trial.items.map(
      (r: { account_id: string; debit: string; credit: string }) => [
        r.account_id,
        micros(r.debit) - micros(r.credit),
      ],
    ),
  );
  expect(amounts.get(f.accounts.bank)).toBe(40000000n);
  expect(amounts.get(f.accounts.ar)).toBe(60000000n);
  expect(amounts.get(f.accounts.income)).toBe(-100000000n);
  expect([...amounts.values()].reduce((a, b) => a + b, 0n)).toBe(0n);
  for (const suffix of [
    "/accounts",
    `/evidence/${f.foreign_evidence}/download`,
    "/jobs",
    "/search?q=invoice",
    "/exports/journals.csv?start=2026-01-01&end=2026-10-30",
  ]) {
    const denied = await page.request.get(
      `/api/v1/businesses/${f.foreign_business}` + suffix,
    );
    expect(denied.status()).toBe(404);
  }
  const invalid = await page.request.post(base + "/trade-documents", {
    data: {
      ...command.postDataJSON(),
      idempotency_key: crypto.randomUUID(),
      number: "STALE-PHASE8",
    },
    headers: {
      "X-CSRF-Token": command.headers()["x-csrf-token"],
      Origin: new URL(page.url()).origin,
    },
  });
  expect(invalid.status()).toBe(409);
  expect(
    (
      await (
        await page.request.get(base + "/trade-documents", {
          params: { kind: "invoice", as_of: day },
        })
      ).json()
    ).items,
  ).toHaveLength(1);
  expect(errors).toEqual([]);
});
import { execFileSync } from "node:child_process";
import path from "node:path";
test("real sign-up replaces verification code and completes local QR enrollment", async ({
  page,
}) => {
  const email = `browser-${crypto.randomUUID()}@example.com`,
    password = `Browser signup phrase 7! ${crypto.randomUUID()}`;
  async function newestCode() {
    const found = await fetch(
      "http://127.0.0.1:8025/api/v1/search?query=" +
        encodeURIComponent("to:" + email),
    );
    const messages = await found.json();
    expect(messages.messages.length).toBeGreaterThan(0);
    const reply = await fetch(
      "http://127.0.0.1:8025/api/v1/message/" + messages.messages[0].ID,
    );
    return (await reply.json()).Text.replaceAll("\r\n", "\n")
      .split("\n\n")[1]
      .trim();
  }
  await page.goto("/");
  await page.getByText("Create an account", { exact: true }).click();
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Show password" }).click();
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
    "type",
    "text",
  );
  await page.getByRole("button", { name: "Hide password" }).click();
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
    "type",
    "password",
  );
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page.getByLabel("Verification email address")).toHaveValue(
    email,
  );
  const old = await newestCode();
  await page.getByRole("button", { name: "Resend verification code" }).click();
  await expect(
    page.getByRole("button", { name: /Resend available/ }),
  ).toBeDisabled();
  const latest = await newestCode();
  expect(latest).not.toBe(old);
  await page.getByLabel("Token from your email").fill(old);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Request a new code and use the newest email",
  );
  await page.getByLabel("Token from your email").fill(latest);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await page.getByLabel("Email address", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  const signedIn = page.waitForResponse((r) =>
    new URL(r.url()).pathname.endsWith("/auth/login"),
  );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  expect((await signedIn).status()).toBe(200);
  const enrolled = page.waitForResponse(
    (r) => new URL(r.url()).pathname.endsWith("/mfa/enroll") && r.ok(),
  );
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const enrollment = await (await enrolled).json();
  await expect(page.locator(".mfa-qr svg")).toBeVisible();
  await expect(page.locator(".mfa-qr svg title")).toHaveText(
    "Scan to add LedgerPilot to your authenticator",
  );
  const python =
    process.env.LEDGERPILOT_TEST_PYTHON ||
    path.resolve("../backend/.venv/Scripts/python.exe");
  const code = execFileSync(
    python,
    ["-c", "import sys,pyotp; print(pyotp.TOTP(sys.stdin.read()).now())"],
    { input: enrollment.secret, encoding: "utf8", windowsHide: true },
  ).trim();
  await page.getByLabel("Six-digit authenticator code").fill(code);
  await page.getByRole("button", { name: "Verify authenticator" }).click();
  await expect(
    page.getByRole("heading", { name: "Save your recovery codes" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "I've saved my codes" }).click();
  await expect(
    page.getByRole("heading", { name: "Where would you like to work?" }),
  ).toBeVisible();
});
