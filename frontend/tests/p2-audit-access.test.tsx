import { afterEach, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { api } from "../src/api/runtime";
import { AuditRecipient } from "../src/features/audit-access";

afterEach(() => vi.restoreAllMocks());

it("opens only the named workpaper and explains snapshot scope", async () => {
  const get = vi.spyOn(api, "GET").mockResolvedValue({
    data: {
      report_id: "report-id",
      as_of: "2026-10-31",
      currency: "USD",
      basis: "accrual",
    },
    response: new Response(null, { status: 200 }),
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  try {
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AuditRecipient accountId="recipient-id" />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(get).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Share reference"), {
      target: { value: "grant-id" },
    });
    fireEvent.change(screen.getByLabelText("Report reference"), {
      target: { value: "report-id" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open shared report" }));
    await waitFor(() =>
      expect(get).toHaveBeenCalledWith(
        "/api/v1/audit-access/{grant_id}/reports/{report_id}",
        { params: { path: { grant_id: "grant-id", report_id: "report-id" } } },
      ),
    );
    expect(
      await screen.findByText(/does not grant access to linked documents/),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Download workpaper" }),
    ).toBeEnabled();
  } finally {
    client.clear();
  }
});
