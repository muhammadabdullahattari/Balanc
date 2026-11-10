import { beforeAll, afterAll, afterEach, describe, it, expect } from "vitest";
import {
  render,
  screen,
  within,
  waitFor,
  cleanup,
  configure,
  fireEvent,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFile } from "node:fs/promises";
import makeFetchCookie from "fetch-cookie";
import { CookieJar } from "tough-cookie";
import { QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import type { components } from "../src/api/generated/schema";
let r: typeof import("../src/api/runtime"),
  c: typeof import("../src/features/context");
let f: {
  email: string;
  password: string;
  recovery: string[];
  business: components["schemas"]["BusinessRead"];
  accounts: Record<string, string>;
  party: string;
  opening_business: components["schemas"]["BusinessRead"];
  opening_accounts: Record<string, string>;
  opening_party: string;
};
configure({ asyncUtilTimeout: 10000 });
const originalFetch = globalThis.fetch,
  user = userEvent.setup(),
  day = "2026-10-30";
beforeAll(async () => {
  f = JSON.parse(await readFile(process.env.LEDGERPILOT_LIVE_FIXTURE!, "utf8"));
  const jar = new CookieJar();
  const cookies = makeFetchCookie(originalFetch, jar);
  let nextRequest = 0;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    request.headers.set("Origin", "http://127.0.0.1:8000");
    // Keep acceptance below the real 120 requests/minute user-business limit, including seed traffic.
    const slot = Math.max(Date.now(), nextRequest);
    nextRequest = slot + 800;
    await new Promise((resolve) =>
      setTimeout(resolve, Math.max(0, slot - Date.now())),
    );
    request.signal.throwIfAborted();
    return cookies(request).then(async (response) => {
      if (response.status === 422)
        console.error("Validation fields", await response.clone().json());
      if (response.status === 403) {
        const header = request.headers.get("x-csrf-token");
        const token = (await jar.getCookies(request.url)).find(
          (c) => c.key === "__Host-lp_csrf",
        )?.value;
        console.error(
          "403",
          new URL(request.url).pathname,
          "headerMatchesCookie",
          header === token,
        );
      }
      return response;
    });
  };
  document.addEventListener(
    "invalid",
    (event) => {
      const e = event.target as HTMLInputElement;
      console.error(
        "Invalid form control",
        e.name,
        e.type,
        e.validationMessage,
      );
    },
    true,
  );
  r = await import("../src/api/runtime");
  c = await import("../src/features/context");
});
afterEach(() => {
  if (screen.queryAllByRole("alert").length)
    console.error(
      "Visible errors:",
      screen
        .queryAllByRole("alert")
        .map((e) => e.textContent)
        .join(" | "),
    );
  cleanup();
  r.queryClient.clear();
});
afterAll(() => {
  globalThis.fetch = originalFetch;
});
function show(component: ReactElement) {
  return render(
    <QueryClientProvider client={r.queryClient}>
      <c.BusinessContext.Provider value={f.business}>
        <c.AccessContext.Provider
          value={{ role: "accountant", can_post: false, can_invite: false }}
        >
          {component}
        </c.AccessContext.Provider>
      </c.BusinessContext.Provider>
    </QueryClientProvider>,
  );
}
const path = () => ({ business_id: f.business.id });
const noAlerts = () =>
  waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
async function createTrade(
  kind: "invoice" | "bill",
  number: string,
  amount: string,
  draft = false,
  creditFor?: string,
) {
  const { Trade } = await import("../src/features/trade");
  show(<Trade kind={kind} />);
  await user.click(
    screen.getByRole("button", { name: `New ${kind}`, exact: true }),
  );
  const d = within(screen.getByRole("dialog"));
  const party = d.getByLabelText(kind === "invoice" ? "Customer" : "Supplier");
  await waitFor(() =>
    expect(party.querySelectorAll("option").length).toBeGreaterThan(1),
  );
  if (creditFor) {
    await user.selectOptions(
      d.getByLabelText("Document type", { exact: true }),
      "customer_credit",
    );
    await user.selectOptions(
      d.getByLabelText("Original document (credit note)"),
      creditFor,
    );
  }
  await user.type(d.getByLabelText("Document number"), number);
  await user.selectOptions(party, f.party);
  for (const [label, value] of [
    ["Accounting date", day],
    ["Due date", "2026-11-15"],
  ]) {
    await user.clear(d.getByLabelText(label));
    await user.type(d.getByLabelText(label), value);
  }
  await user.selectOptions(
    d.getByLabelText(
      kind === "invoice"
        ? "Receivables control account"
        : "Payables control account",
    ),
    f.accounts[kind === "invoice" ? "ar" : "ap"],
  );
  await user.type(d.getByLabelText("Description"), `Live ${kind}`);
  await user.selectOptions(
    d.getByLabelText("Account", { exact: true }),
    f.accounts[kind === "invoice" ? "income" : "cost"],
  );
  await user.type(d.getByLabelText("Amount", { exact: true }), amount);
  await user.click(d.getByRole("button", { name: "Review before posting" }));
  await d.findByRole("button", { name: "Approve & post" });
  await user.click(
    d.getByRole("button", { name: draft ? "Save as draft" : "Approve & post" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  if (!draft) await screen.findByText(number);
  await noAlerts();
}
describe("frontend with real HTTP, cookie sessions and PostgreSQL", () => {
  it("signs in with MFA recovery", async () => {
    const { Auth } = await import("../src/features/auth");
    show(<Auth />);
    await user.type(screen.getByLabelText("Email address"), f.email);
    await user.type(
      screen.getByLabelText("Password", { exact: true }),
      f.password,
    );
    await user.click(
      screen.getByRole("button", { name: "Sign in", exact: true }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Use a recovery code" }),
    );
    await user.type(
      screen.getByLabelText("Recovery code", { exact: true }),
      f.recovery[0],
    );
    await user.click(
      screen.getByRole("button", { name: "Verify and sign in" }),
    );
    await waitFor(async () =>
      expect((await r.api.GET("/api/v1/auth/me")).data?.email).toBe(f.email),
    );
    await noAlerts();
  });
  it("previews and posts an invoice", async () => {
    await createTrade("invoice", "LIVE-INV-001", "100.00");
    const data = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: { path: path(), query: { as_of: day, kind: "invoice" } },
      }),
    );
    expect(data.items[0].total).toMatch(/^100\.0+$/);
    expect(data.items[0].outstanding).toMatch(/^100\.0+$/);
  });
  it("confirms receipt before changing outstanding balance", async () => {
    const { Trade } = await import("../src/features/trade");
    show(<Trade kind="invoice" />);
    await user.click(
      await screen.findByRole("button", { name: /View & record/ }),
    );
    const d = within(screen.getByRole("dialog"));
    await waitFor(() =>
      expect(
        d.getByLabelText("Bank account").querySelectorAll("option").length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(d.getByLabelText("Bank account"), f.accounts.bank);
    fireEvent.change(d.getByLabelText("Payment date"), {
      target: { value: day },
    });
    await user.click(d.getByRole("button", { name: "Review payment" }));
    await d.findByRole("button", { name: "Approve payment record" });
    const before = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: { path: path(), query: { as_of: day, kind: "invoice" } },
      }),
    );
    expect(before.items[0].outstanding).toMatch(/^100\.0+$/);
    await user.click(d.getByRole("button", { name: "Approve payment record" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const after = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: { path: path(), query: { as_of: day, kind: "invoice" } },
      }),
    );
    expect(after.items[0].outstanding).toMatch(/^0\.0+$/);
  });
  it("posts a supplier bill", async () => {
    await createTrade("bill", "LIVE-BILL-001", "80.00");
    const data = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: { path: path(), query: { as_of: day, kind: "bill" } },
      }),
    );
    expect(data.items[0].outstanding).toMatch(/^80\.0+$/);
  });
  it("saves, edits and issues a prepared draft", async () => {
    await createTrade("invoice", "LIVE-DRAFT-001", "20.00", true);
    cleanup();
    r.queryClient.clear();
    const { Drafts } = await import("../src/features/drafts");
    show(<Drafts />);
    await user.click(
      await screen.findByRole("button", { name: "Review draft" }),
    );
    const d = within(screen.getByRole("dialog"));
    await user.click(d.getByText("Edit prepared document"));
    const input = d.getByLabelText("Document number");
    await user.clear(input);
    await user.type(input, "LIVE-DRAFT-EDITED");
    await user.click(d.getByRole("button", { name: "Save reviewed changes" }));
    await waitFor(() =>
      expect(d.getByRole("heading")).toHaveTextContent("LIVE-DRAFT-EDITED"),
    );
    await user.click(d.getByRole("button", { name: "Approve & issue" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await noAlerts();
  });
  it("previews and activates a category rule", async () => {
    const { Rules } = await import("../src/features/rules");
    show(<Rules />);
    await user.type(screen.getByLabelText("Rule name"), "Live office rule");
    await user.type(
      screen.getByLabelText("Bank description contains"),
      "Office",
    );
    await waitFor(() =>
      expect(
        screen.getByLabelText("Suggested account").querySelectorAll("option")
          .length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(
      screen.getByLabelText("Suggested account"),
      f.accounts.cost,
    );
    await user.click(
      screen.getByRole("button", { name: "Create inactive rule" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Preview & manage" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "Activate reviewed rule" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await screen.findByText("Active");
    await noAlerts();
  });
  it("records a customer advance", async () => {
    const { Advances } = await import("../src/features/advances");
    show(<Advances />);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Contact").querySelectorAll("option").length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(screen.getByLabelText("Contact"), f.party);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Bank account").querySelectorAll("option").length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(
      screen.getByLabelText("Bank account"),
      f.accounts.bank,
    );
    await user.selectOptions(
      screen.getByLabelText("Deposit control account"),
      f.accounts.deposit,
    );
    fireEvent.change(screen.getByLabelText("Accounting date"), {
      target: { value: day },
    });
    await user.type(screen.getByLabelText("Amount", { exact: true }), "15.00");
    await user.click(screen.getByRole("button", { name: "Review entry" }));
    await user.click(
      await screen.findByRole("button", { name: "Approve entry" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const data = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/advances", {
        params: { path: path(), query: { as_of: day } },
      }),
    );
    expect(data.items[0].unapplied).toMatch(/^15\.0+$/);
  });

  it("creates a customer credit and records a linked refund through the payment screen", async () => {
    const docs = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: { path: path(), query: { as_of: day, kind: "invoice" } },
      }),
    );
    const original = docs.items.find((d) => d.number === "LIVE-INV-001")!;
    await createTrade(
      "invoice",
      "LIVE-CREDIT-001",
      "25.00",
      false,
      original.id,
    );
    cleanup();
    r.queryClient.clear();
    const credits = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: {
          path: path(),
          query: { as_of: day, kind: "customer_credit" },
        },
      }),
    );
    const { Refunds } = await import("../src/features/refunds");
    show(<Refunds />);
    await user.click(
      await screen.findByRole("button", { name: "Record refund" }),
    );
    const d = within(screen.getByRole("dialog"));
    await user.selectOptions(
      d.getByLabelText("Original document"),
      original.id,
    );
    await user.type(
      d.getByLabelText("Issued credit-note reference"),
      credits.items[0].id,
    );
    fireEvent.change(d.getByLabelText("Refund date"), {
      target: { value: day },
    });
    await user.type(d.getByLabelText("Refund amount"), "25.00");
    await user.click(d.getByRole("button", { name: "Review refund" }));
    await user.click(
      await d.findByRole("button", { name: "Approve refund record" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const payments = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/payments", {
        params: { path: path() },
      }),
    );
    expect(
      payments.items.some(
        (p) =>
          p.kind === "customer_refund" &&
          p.allocations[0].credit_note_id === credits.items[0].id,
      ),
    ).toBe(true);
  });
  it("records a loan drawdown and verifies the loan subledger", async () => {
    const { Loans } = await import("../src/features/loans");
    show(<Loans />);
    await user.click(
      screen.getByRole("button", { name: "Add loan", exact: true }),
    );
    let d = within(screen.getByRole("dialog"));
    await waitFor(() =>
      expect(
        d.getByLabelText("Loan control account").querySelectorAll("option")
          .length,
      ).toBeGreaterThan(1),
    );
    await user.type(d.getByLabelText("Loan name"), "Live working-capital loan");
    await waitFor(() =>
      expect(
        d.getByLabelText("Lender / borrower").querySelectorAll("option").length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(d.getByLabelText("Lender / borrower"), f.party);
    await user.selectOptions(
      d.getByLabelText("Loan control account"),
      f.accounts.loan,
    );
    await user.click(d.getByRole("button", { name: "Add loan", exact: true }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(
      await screen.findByRole("button", { name: "Record movement" }),
    );
    d = within(screen.getByRole("dialog"));
    fireEvent.change(d.getByLabelText("Accounting date"), {
      target: { value: day },
    });
    await user.type(d.getByLabelText("Principal"), "200.00");
    await user.selectOptions(d.getByLabelText("Bank account"), f.accounts.bank);
    await user.click(d.getByRole("button", { name: "Review movement" }));
    await user.click(
      await d.findByRole("button", { name: "Confirm movement" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    const loans = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/loans", {
        params: { path: path(), query: { as_of: day } },
      }),
    );
    expect(loans.items[0].principal).toMatch(/^200\.0+$/);
  });
  it("imports CSV evidence, previews and posts a reviewed bank category", async () => {
    const { Banking } = await import("../src/features/banking");
    show(<Banking />);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Bank or card account").querySelectorAll("option")
          .length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(
      screen.getByLabelText("Bank or card account"),
      f.accounts.bank,
    );
    await user.click(screen.getByRole("button", { name: "Import statement" }));
    let d = within(screen.getByRole("dialog"));
    await user.upload(
      d.getByLabelText("CSV statement"),
      new File(
        ["date,description,amount\n2026-10-30,Live independent sale,12.00\n"],
        "live.csv",
        { type: "text/csv" },
      ),
    );
    await waitFor(() =>
      expect(
        d.getByRole("button", { name: "Preview statement" }),
      ).toBeEnabled(),
    );
    // user-event's FileList shim does not update jsdom's internal required-file validity.
    // Submit the real form after asserting its selected file; all HTTP/service behavior is real.
    expect(
      (d.getByLabelText("CSV statement") as HTMLInputElement).files?.length,
    ).toBe(1);
    fireEvent.submit(d.getByLabelText("CSV statement").closest("form")!);
    await user.click(await d.findByRole("button", { name: "Confirm import" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(
      await screen.findByRole("button", { name: "Review", exact: true }),
    );
    d = within(screen.getByRole("dialog"));
    await user.selectOptions(
      d.getByLabelText("Account (for categorization)"),
      f.accounts.income,
    );
    await user.type(
      d.getByLabelText("Review reason"),
      "Reviewed source evidence",
    );
    await user.click(
      d.getByRole("button", { name: "Preview accounting impact" }),
    );
    await user.click(await d.findByRole("button", { name: "Confirm review" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await noAlerts();
  });
  it("loads the actual tax workpaper and cited profit lookup", async () => {
    const { Reports } = await import("../src/features/reports");
    show(<Reports />);
    await user.click(
      await screen.findByRole("button", { name: "Explain this profit figure" }),
    );
    await screen.findByText(/This is a cited report lookup/);
    await user.click(screen.getByRole("tab", { name: "Tax support" }));
    await screen.findByRole("heading", { name: "Tax support workpaper" });
    await noAlerts();
  });

  it("imports reviewed opening balances into an empty independent business", async () => {
    const original = f.business;
    f.business = f.opening_business;
    try {
      const { Openings } = await import("../src/features/openings");
      show(<Openings />);
      await waitFor(() =>
        expect(
          screen
            .getByLabelText("Opening-equity account")
            .querySelectorAll("option").length,
        ).toBeGreaterThan(1),
      );
      await user.upload(
        screen.getByLabelText("Opening balances CSV"),
        new File(
          [
            `kind,party_id,reference,original_date,due_date,amount,control_account_code\ncustomer,${f.opening_party},OPEN-001,2026-09-01,2026-09-30,55.00,ar\n`,
          ],
          "opening.csv",
          { type: "text/csv" },
        ),
      );
      await user.selectOptions(
        screen.getByLabelText("Opening-equity account"),
        "opening_equity",
      );
      await user.selectOptions(
        screen.getByLabelText("Baseline account 1"),
        "ar",
      );
      await user.type(screen.getByLabelText("Signed balance 1"), "55.00");
      await user.selectOptions(
        screen.getByLabelText("Baseline account 2"),
        "opening_equity",
      );
      await user.type(screen.getByLabelText("Signed balance 2"), "-55.00");
      await user.type(
        screen.getByLabelText("Accountant review reason"),
        "Reviewed against prior accountant trial balance",
      );
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Validate & preview import" }),
        ).toBeEnabled(),
      );
      expect(
        (screen.getByLabelText("Opening balances CSV") as HTMLInputElement)
          .files?.length,
      ).toBe(1);
      fireEvent.submit(
        screen.getByLabelText("Opening balances CSV").closest("form")!,
      );
      await user.click(
        await screen.findByRole("button", { name: "Approve opening balances" }),
      );
      await screen.findByText("OPEN-001");
      await noAlerts();
    } finally {
      f.business = original;
    }
  });

  it("uploads actual statement evidence, saves a review note and signs off its ledger balance", async () => {
    const { Evidence } = await import("../src/features/evidence");
    show(<Evidence />);
    await user.click(screen.getByRole("button", { name: "Upload document" }));
    let d = within(screen.getByRole("dialog"));
    const fileInput = d.getByLabelText(
      /Choose a receipt, invoice or statement/,
    );
    await user.upload(
      fileInput,
      new File(
        ["account,date,closing_balance\nBank,2026-10-30,302.00\n"],
        "statement.csv",
        { type: "text/csv" },
      ),
    );
    fireEvent.submit(fileInput.closest("form")!);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    let evidenceId = "";
    await waitFor(
      async () => {
        const data = await r.result(
          r.api.GET("/api/v1/businesses/{business_id}/evidence", {
            params: { path: path() },
          }),
        );
        const item = data.items.find((e) => e.filename === "statement.csv");
        expect(
          item && ["clean", "extracted", "unsupported"].includes(item.status),
        ).toBe(true);
        evidenceId = item!.id;
      },
      { timeout: 25000 },
    );
    await user.click(
      await screen.findByRole("button", { name: "statement.csv" }),
    );
    d = within(screen.getByRole("dialog"));
    await user.type(
      await d.findByLabelText("Add a review note"),
      "Reviewed bank closing balance",
    );
    await user.click(d.getByRole("button", { name: "Save note" }));
    await d.findByText("Reviewed bank closing balance");
    cleanup();
    r.queryClient.clear();
    const { Reconciliation } = await import("../src/features/reconciliation");
    show(<Reconciliation />);
    await waitFor(() =>
      expect(
        screen
          .getByLabelText("Supporting statement document")
          .querySelectorAll("option").length,
      ).toBeGreaterThan(1),
    );
    await user.selectOptions(
      screen.getByLabelText("Bank or card account"),
      f.accounts.bank,
    );
    await user.selectOptions(
      screen.getByLabelText("Supporting statement document"),
      evidenceId,
    );
    await user.type(
      screen.getByLabelText("Statement closing balance (USD)"),
      "302.00",
    );
    await user.click(screen.getByRole("button", { name: "Review sign-off" }));
    await user.click(
      await screen.findByRole("button", { name: "Approve sign-off" }),
    );
    await screen.findByRole("heading", { name: "Statement signed off." });
    await noAlerts();
  });
  it("prepares an optional close task and confirms period lock", async () => {
    const { Close } = await import("../src/features/close");
    show(<Close />);
    await waitFor(() =>
      expect(
        screen.getByLabelText("Accounting period").querySelectorAll("option")
          .length,
      ).toBeGreaterThan(1),
    );
    const period = (
      screen.getByLabelText("Accounting period") as HTMLSelectElement
    ).options[1].value;
    await user.selectOptions(
      screen.getByLabelText("Accounting period"),
      period,
    );
    await user.click(screen.getByRole("button", { name: "Add close task" }));
    let d = within(screen.getByRole("dialog"));
    await waitFor(() =>
      expect(
        d.getByLabelText("Assign to member").querySelectorAll("option").length,
      ).toBeGreaterThan(1),
    );
    const self = (await r.result(r.api.GET("/api/v1/auth/me"))).id;
    await user.selectOptions(d.getByLabelText("Assign to member"), self);
    await user.type(d.getByLabelText("Task name"), "Reconcile bank");
    await user.click(d.getByLabelText("Required before close"));
    await user.click(d.getByRole("button", { name: "Create task" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(
      await screen.findByRole("button", { name: "Review task" }),
    );
    d = within(screen.getByRole("dialog"));
    await user.selectOptions(d.getByLabelText("Status"), "prepared");
    await user.type(
      d.getByLabelText("Review comment"),
      "Prepared supporting schedule",
    );
    await user.click(d.getByRole("button", { name: "Save review" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Lock period" }));
    await user.click(
      await screen.findByRole("button", { name: "Confirm period lock" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await screen.findByRole("button", { name: "Reopen period" });
  });

  it("holds supplier bank-detail proposals and shows masked change alerts", async () => {
    const { People } = await import("../src/features/directory");
    show(<People />);
    await user.click(
      await screen.findByRole("button", { name: "Synthetic multi-role party" }),
    );
    const d = within(screen.getByRole("dialog"));
    await user.click(d.getByText("Supplier bank-detail change review"));
    await user.type(d.getByLabelText("Account holder"), "Synthetic supplier");
    await user.type(d.getByLabelText("Bank name"), "Synthetic bank");
    await user.type(
      d.getByLabelText("New account reference"),
      "SYNTHETIC-12345678",
    );
    await user.type(
      d.getByLabelText("Change reason"),
      "Supplier requested an independently verified update",
    );
    await user.click(d.getByRole("button", { name: "Review proposed change" }));
    await user.click(
      await d.findByRole("button", { name: "Record held proposal" }),
    );
    await d.findByText("Proposal recorded for independent verification.");
    const changes = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/beneficiary-changes", {
        params: { path: path() },
      }),
    );
    expect(changes.items[0].status).toBe("held");
    expect(changes.items[0].masked_reference).not.toContain("12345678");
    await noAlerts();
  });
  it("flags and resolves a document dispute without editing its posted total", async () => {
    const { Trade } = await import("../src/features/trade");
    show(<Trade kind="invoice" />);
    const number = await screen.findByText("LIVE-INV-001");
    const row = number.closest("tr")!;
    await user.click(
      within(row).getByRole("button", { name: /View & record/ }),
    );
    const d = within(screen.getByRole("dialog"));
    await user.click(d.getByText("Flag a document dispute"));
    await user.type(
      d.getByLabelText("Dispute reason"),
      "Customer requested supporting evidence",
    );
    await user.click(
      d.getByRole("button", { name: "Flag dispute", exact: true }),
    );
    await d.findByText("Dispute state updated.");
    await user.click(
      d.getByRole("button", { name: "Resolve dispute", exact: true }),
    );
    await waitFor(() =>
      expect(
        d.getByRole("button", { name: "Flag dispute", exact: true }),
      ).toBeEnabled(),
    );
    const data = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trade-documents", {
        params: { path: path(), query: { as_of: day, kind: "invoice" } },
      }),
    );
    const original = data.items.find((v) => v.number === "LIVE-INV-001")!;
    expect(original.disputed).toBe(false);
    expect(original.total).toMatch(/^100\.0+$/);
    await noAlerts();
  });
  it("ties trial balance and exports real CSV and ZIP evidence", async () => {
    const trial = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/trial-balance", {
        params: { path: path(), query: { as_of: day } },
      }),
    );
    expect(trial.balanced).toBe(true);
    const csv = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/exports/journals.csv", {
        params: { path: path(), query: { start: "2026-01-01", end: day } },
        parseAs: "text",
      }),
    );
    expect(csv).toContain("journal");
    const zip = await r.result(
      r.api.GET("/api/v1/businesses/{business_id}/exports/evidence-pack.zip", {
        params: { path: path(), query: { start: "2026-01-01", end: day } },
        parseAs: "arrayBuffer",
      }),
    );
    expect(new Uint8Array(zip).slice(0, 2)).toEqual(new Uint8Array([80, 75]));
  });
});

it("enforces exact credentialed CORS for the web origin", async () => {
  const endpoint = "http://127.0.0.1:8000/api/v1/businesses";
  const preflight = (origin: string) =>
    originalFetch(endpoint, {
      method: "OPTIONS",
      headers: {
        Origin: origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token",
      },
    });
  const allowed = await preflight("http://127.0.0.1:5173");
  expect(allowed.status).toBe(200);
  expect(allowed.headers.get("access-control-allow-origin")).toBe(
    "http://127.0.0.1:5173",
  );
  expect(allowed.headers.get("access-control-allow-credentials")).toBe("true");
  const denied = await preflight("http://untrusted.invalid");
  expect(denied.status).toBe(400);
  expect(denied.headers.get("access-control-allow-origin")).toBeNull();
});
