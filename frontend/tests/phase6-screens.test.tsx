import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../src/features/dashboard";
import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Auth } from "../src/features/auth";
import { Accounts } from "../src/features/directory";
import { BusinessContext, type Business } from "../src/features/context";
import { api } from "../src/api/runtime";
const business = {
  id: "synthetic-business",
  timezone: "UTC",
  currency: "USD",
  basis: "accrual",
} as Business;
const clients: QueryClient[] = [];
function mount(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <BusinessContext.Provider value={business}>
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
