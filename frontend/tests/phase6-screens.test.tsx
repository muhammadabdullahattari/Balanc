import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../src/features/dashboard";
import { Activity } from "../src/features/activity";
import { Projects } from "../src/features/projects";
import { Reconciliation } from "../src/features/reconciliation";
import { Reports } from "../src/features/reports";
import { Openings } from "../src/features/openings";
import { EmailOperations } from "../src/features/email-operations";
import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Auth } from "../src/features/auth";
import { Accounts } from "../src/features/directory";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";
import { api } from "../src/api/runtime";
const business = {
  id: "synthetic-business",
  timezone: "UTC",
  currency: "USD",
  basis: "accrual",
} as Business;
const clients: QueryClient[] = [];
function mount(node: React.ReactNode, workspace = business) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <BusinessContext.Provider value={workspace}>
        {node}
      </BusinessContext.Provider>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  vi.restoreAllMocks();
  clients.forEach((c) => c.clear());
  clients.length = 0;
});
function reply(data: unknown) {
  return { data, response: new Response(null, { status: 200 }) };
}
describe("Phase 6 screen journeys with isolated API doubles", () => {
  it("shows diagnostics without mutation controls to a delegated reviewer", async () => {
    const get = vi.spyOn(api, "GET").mockResolvedValue(
      reply({
        counts: { failed: 1 },
        attention: [
          {
            id: "failed-job",
            kind: "overdue_invoices",
            state: "failed",
            attempts: 3,
            outcome_code: "ERR_SMTP_REJECTED",
            created_at: "2026-11-10T12:00:00Z",
            feedback: [],
          },
        ],
        grants: [],
      }) as any,
    );
    mount(
      <AccessContext.Provider
        value={{ role: "reviewer", can_post: false, can_invite: false }}
      >
        <EmailOperations />
      </AccessContext.Provider>,
    );
    expect(await screen.findByText("ERR_SMTP_REJECTED")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Record feedback" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Grant diagnostics access" }),
    ).toBeNull();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("compares a pasted legacy trial balance against a financial import", async () => {
    vi.spyOn(api, "GET").mockImplementation(
      (path: string) =>
        Promise.resolve(
          reply({
            items: path.endsWith("/imports")
              ? [
                  {
                    id: "opening-batch",
                    mode: "opening",
                    cutover_date: "2026-10-01",
                  },
                ]
              : [],
            next_cursor: null,
          }),
        ) as any,
    );
    const post = vi.spyOn(api, "POST").mockResolvedValue(
      reply({
        as_of: "2026-10-31",
        balanced: false,
        controls: { balanced: true },
        items: [
          {
            account_id: "cash",
            code: "bank",
            expected: "5",
            actual: "4",
            difference: "-1",
          },
        ],
      }) as any,
    );
    mount(
      <AccessContext.Provider
        value={{ role: "accountant", can_post: true, can_invite: false }}
      >
        <Openings />
      </AccessContext.Provider>,
    );
    await screen.findByRole("option", { name: /2026-10-01 · opening/ });
    await userEvent.selectOptions(
      screen.getByLabelText("Financial import batch"),
      "opening-batch",
    );
    fireEvent.change(screen.getByLabelText("Comparison date"), {
      target: { value: "2026-10-31" },
    });
    fireEvent.change(
      screen.getByLabelText("Legacy account codes and signed balances"),
      {
        target: { value: "bank\t5\nequity\t-5" },
      },
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Compare trial balances" }),
    );
    expect(
      await screen.findByText(/account differences need review/i),
    ).toBeVisible();
    expect(post).toHaveBeenCalledWith(
      "/api/v1/businesses/{business_id}/imports/{batch_id}/parallel-comparison",
      expect.objectContaining({
        body: {
          as_of: "2026-10-31",
          expected_trial: { bank: "5", equity: "-5" },
        },
      }),
    );
  });

  it("shows a projected cash shortfall after selecting the stress scenario", async () => {
    const post = vi.spyOn(api, "POST").mockResolvedValue(
      reply({
        report_id: "cash-run",
        metric: "forecast_first_shortfall",
        value: "2026-11-07",
      }) as any,
    );
    const get = vi.spyOn(api, "GET").mockImplementation(
      (path: string) =>
        Promise.resolve(
          reply(
            path.endsWith("/accounts")
              ? {
                  items: [
                    {
                      id: "cash",
                      name: "Operating bank",
                      kind: "asset",
                      active: true,
                    },
                  ],
                }
              : path.endsWith("/forecast")
                ? {
                    report_id: "cash-run",
                    confidence: "low",
                    assumptions: [],
                    weeks: [
                      {
                        week: 1,
                        start_date: "2026-11-01",
                        end_date: "2026-11-07",
                        receipts: "75",
                        payments: "125",
                        closing_cash: "-50",
                        shortfall: true,
                      },
                    ],
                  }
                : { items: [], net_profit: "0", report_id: "profit-run" },
          ),
        ) as any,
    );
    mount(<Reports />, { ...business, p2_insights_available: true });
    await userEvent.click(screen.getByRole("tab", { name: "Cash forecast" }));
    await userEvent.selectOptions(
      screen.getByLabelText("Cash scenario"),
      "stress",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Cash account"),
      "cash",
    );
    expect(
      await screen.findByText(/first projected shortfall.*2026-11-07/i),
    ).toBeVisible();
    expect(get).toHaveBeenCalledWith(
      "/api/v1/businesses/{business_id}/reports/forecast",
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            receipt_percent: 75,
            payment_percent: 125,
          }),
        }),
      }),
    );
    await userEvent.click(
      screen.getByRole("button", {
        name: "When is the first projected shortfall?",
      }),
    );
    expect(await screen.findByText(/source report cash-run\./i)).toBeVisible();
    expect(post).toHaveBeenCalledWith(
      "/api/v1/businesses/{business_id}/assistant/report-answer",
      expect.objectContaining({
        body: { report_id: "cash-run", metric: "forecast_first_shortfall" },
      }),
    );
  });

  it("blocks reconciliation approval when the server preview reports a difference", async () => {
    vi.spyOn(api, "GET").mockImplementation(
      (path: string) =>
        Promise.resolve(
          reply(
            path.endsWith("/accounts")
              ? {
                  items: [
                    {
                      id: "cash",
                      name: "Operating bank",
                      kind: "asset",
                      active: true,
                    },
                  ],
                }
              : path.endsWith("/evidence")
                ? {
                    items: [
                      {
                        id: "statement",
                        filename: "bank.csv",
                        status: "clean",
                      },
                    ],
                  }
                : { version: 1 },
          ),
        ) as any,
    );
    const post = vi.spyOn(api, "POST").mockResolvedValue(
      reply({
        ledger_balance: "0",
        statement_balance: "1",
        outstanding: "0",
        unexplained_difference: "-1",
        pending_bank_rows: 0,
        ready: false,
      }) as any,
    );
    mount(
      <AccessContext.Provider
        value={{ role: "accountant", can_post: true, can_invite: false }}
      >
        <Reconciliation />
      </AccessContext.Provider>,
      { ...business, p2_reconciliation_available: true },
    );
    await screen.findByRole("option", { name: "Operating bank" });
    await userEvent.selectOptions(
      screen.getByLabelText("Bank or card account"),
      "cash",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Supporting statement document"),
      "statement",
    );
    await userEvent.type(
      screen.getByLabelText("Statement closing balance (USD)"),
      "1",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Review sign-off" }),
    );
    expect(
      await screen.findByText(/resolve the statement difference/i),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Approve sign-off" }),
    ).toBeDisabled();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/api/v1/businesses/{business_id}/statements/preview",
      expect.objectContaining({
        body: expect.objectContaining({ closing_balance: "1" }),
      }),
    );
  });

  it("tags a posted expense in the Projects workspace", async () => {
    vi.spyOn(api, "GET").mockImplementation(
      (path: string) =>
        Promise.resolve(
          reply(
            path.endsWith("/ledger-lines")
              ? {
                  items: [
                    {
                      id: "line-1",
                      accounting_date: "2026-11-01",
                      memo: "Supplier expense",
                      account_name: "Expenses",
                      debit: "50",
                      credit: "0",
                      project_id: null,
                      location: null,
                      billable: false,
                      billing_state: null,
                      eligible: true,
                    },
                  ],
                }
              : {
                  items: [
                    {
                      id: "project-1",
                      name: "Workshop",
                      customer_id: "customer-1",
                    },
                  ],
                },
          ),
        ) as any,
    );
    const post = vi
      .spyOn(api, "POST")
      .mockResolvedValue(reply({ status: "tagged" }) as any);
    mount(
      <AccessContext.Provider
        value={
          { role: "bookkeeper" } as React.ContextType<typeof AccessContext>
        }
      >
        <Projects />
      </AccessContext.Provider>,
      { ...business, p2_projects_available: true },
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Tag line" }),
    );
    await userEvent.selectOptions(
      screen.getByRole("dialog").querySelector("select")!,
      "project-1",
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: /bill this expense/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/api/v1/businesses/{business_id}/projects/ledger-lines/{line_id}/tag",
        {
          params: { path: { business_id: business.id, line_id: "line-1" } },
          body: { project_id: "project-1", location: null, billable: true },
        },
      ),
    );
  });
  it("queues opted-in workspace email and identifies an uncertain delivery", async () => {
    vi.spyOn(api, "GET").mockImplementation(
      (path: string) =>
        Promise.resolve(
          reply(
            path.endsWith("/notification-preferences")
              ? {
                  version: 1,
                  reminders: true,
                  marketing: false,
                  email_reminders: true,
                  email_delivery_available: true,
                  quiet_start: 21,
                  quiet_end: 8,
                }
              : path.endsWith("/notification-email-history")
                ? {
                    items: [
                      {
                        job_id: "synthetic-job",
                        kind: "missing_evidence",
                        state: "uncertain",
                        attempts: 1,
                        code: "ERR_SEND_INTERRUPTED",
                      },
                    ],
                  }
                : { items: [], next_cursor: null },
          ),
        ) as any,
    );
    const queued = vi.spyOn(api, "POST").mockResolvedValue(
      reply({
        created: 1,
        channel: "in_app",
        external_messages_sent: 0,
        email_queued: 2,
      }) as any,
    );
    mount(<Activity />);
    expect(await screen.findByText("Delivery needs review")).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "Review and queue today's emails" }),
    );
    expect(await screen.findByText("2 email jobs queued.")).toBeVisible();
    expect(queued).toHaveBeenCalledWith(
      "/api/v1/businesses/{business_id}/reminders/review",
      expect.objectContaining({
        params: { path: { business_id: business.id } },
      }),
    );
  });
  it("rejects empty sign-in without sending credentials", async () => {
    const post = vi.spyOn(api, "POST");
    mount(<Auth />);
    fireEvent.submit(
      screen
        .getByRole("button", { name: "Sign in", exact: true })
        .closest("form")!,
    );
    expect(
      await screen.findByText("Enter a valid email address."),
    ).toBeVisible();
    expect(screen.getByText("Enter your password.")).toBeVisible();
    expect(post).not.toHaveBeenCalled();
  });
  it("switches registration, verification and recovery without API side effects", async () => {
    const post = vi.spyOn(api, "POST");
    mount(<Auth />);
    await userEvent.click(screen.getByText("Create an account"));
    expect(screen.getByLabelText(/^Password/)).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
    await userEvent.click(screen.getByText("Back to sign in"));
    await userEvent.click(screen.getByText("Verify email"));
    expect(screen.getByLabelText("Token from your email")).toBeRequired();
    await userEvent.click(screen.getByText("Back to sign in"));
    await userEvent.click(screen.getByText("Forgot password?"));
    expect(screen.getByLabelText("Email address")).toBeRequired();
    expect(post).not.toHaveBeenCalled();
  });
  it("shows pending accounts then the empty guidance", async () => {
    let finish!: (value: any) => void;
    vi.spyOn(api, "GET").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }) as any,
    );
    mount(<Accounts />);
    expect(screen.getByRole("status")).toBeVisible();
    finish(reply({ items: [], next_cursor: null }));
    expect(await screen.findByText("No accounts yet")).toBeVisible();
  });
  it("shows populated accounts and opens the actual classification form", async () => {
    vi.spyOn(api, "GET").mockResolvedValue(
      reply({
        items: [
          {
            id: "cash",
            code: "100",
            name: "Synthetic cash",
            kind: "asset",
            control: null,
            active: true,
          },
        ],
        next_cursor: null,
      }) as any,
    );
    mount(<Accounts />);
    expect(await screen.findByText("Synthetic cash")).toBeVisible();
    expect(screen.getByRole("button", { name: /Next page/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /New account/ }));
    expect(screen.getByRole("dialog", { name: "New account" })).toBeVisible();
    await userEvent.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
  it("renders a rejected account read and retries only after user action", async () => {
    const get = vi.spyOn(api, "GET").mockResolvedValue({
      error: {
        error: { message: "Temporarily unavailable" },
        request_id: "synthetic-ref",
      },
      response: new Response(null, { status: 503 }),
    } as any);
    mount(<Accounts />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Temporarily unavailable",
    );
    expect(get).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });
});

it("renders authoritative dashboard totals, chart alternative and reporting basis", async () => {
  vi.spyOn(api, "GET").mockImplementation(
    (path: string) =>
      Promise.resolve(
        reply(
          path.endsWith("/accounts")
            ? {
                items: [
                  {
                    id: "bank",
                    name: "Synthetic bank",
                    kind: "asset",
                    active: true,
                    control: "bank",
                  },
                ],
                next_cursor: null,
              }
            : {
                currency: "USD",
                cash_balance: "123.45",
                receivables: { total: "10" },
                payables: { total: "20" },
                net_profit: "30",
                as_of: "2026-11-02",
                timezone: "UTC",
                basis: "accrual",
                report_id: "synthetic-report",
                trend: [
                  {
                    start_date: "2026-11-01",
                    income: "100.25",
                    expense: "70.25",
                  },
                ],
              },
        ),
      ) as any,
  );
  mount(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(
      screen.getByLabelText("Cash account").querySelectorAll("option").length,
    ).toBeGreaterThan(1),
  );
  await userEvent.selectOptions(screen.getByLabelText("Cash account"), "bank");
  expect(await screen.findByText("USD 123.45")).toBeVisible();
  expect(screen.getByRole("img", { name: /Monthly income/ })).toBeVisible();
  await userEvent.click(screen.getByText("View exact figures"));
  expect(screen.getByRole("cell", { name: "USD 100.25" })).toBeVisible();
  expect(screen.getByText(/synthetic-report/)).toHaveTextContent(
    "accrual basis",
  );
});
