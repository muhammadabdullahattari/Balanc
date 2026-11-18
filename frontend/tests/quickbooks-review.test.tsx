import { expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { QuickBooksImport } from "../src/features/quickbooks-import";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";
import { api } from "../src/api/runtime";

it("requires a fresh reconciled review whenever the import selection changes", async () => {
  const post = vi.spyOn(api, "POST").mockResolvedValue({
    response: new Response(null, { status: 200 }),
    data: { reconciled: true },
  } as never);
  const client = new QueryClient();
  try {
    render(
      <QueryClientProvider client={client}>
        <AccessContext.Provider
          value={{ role: "owner", can_post: true, can_invite: true }}
        >
          <BusinessContext.Provider value={{ id: "fixture" } as Business}>
            <QuickBooksImport />
          </BusinessContext.Provider>
        </AccessContext.Provider>
      </QueryClientProvider>,
    );
    const live = await screen.findByRole("button", {
      name: "Confirm & Post to Ledger",
    });
    expect(live).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: /Run Pre-Import Review/ }),
    );
    await screen.findByText("BALANCED / RECONCILED");
    expect(live).toBeEnabled();
    fireEvent.click(screen.getByLabelText("Customers"));
    expect(live).toBeDisabled();
    expect(screen.queryByText("BALANCED / RECONCILED")).toBeNull();
  } finally {
    client.clear();
    post.mockRestore();
  }
});
