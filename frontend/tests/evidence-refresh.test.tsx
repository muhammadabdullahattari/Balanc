import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { Evidence } from "../src/features/evidence";
import { BusinessContext, type Business } from "../src/features/context";
import { api } from "../src/api/runtime";

it("refreshes an open evidence panel after processing and stops at its terminal state", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  let detailReads = 0;
  const item = {
    id: "e1",
    filename: "receipt.csv",
    status: "quarantined",
    media_type: "text/csv",
    size: 10,
    extraction: {},
    content_hash: "hash",
  };
  const get = vi.spyOn(api, "GET").mockImplementation(async (path) => {
    let data: unknown;
    if (path.endsWith("/notes")) data = { items: [], next_cursor: null };
    else if (path.endsWith("/{evidence_id}")) {
      detailReads++;
      data = {
        ...item,
        status: detailReads > 1 ? "extracted" : "quarantined",
        raw_text: detailReads > 1 ? "Fresh extracted receipt" : null,
      };
    } else data = { items: [item], next_cursor: null };
    return { data, response: new Response(null, { status: 200 }) } as any;
  });
  const view = render(
    <QueryClientProvider client={client}>
      <BusinessContext.Provider
        value={{ id: "b1", currency: "USD", timezone: "UTC" } as Business}
      >
        <Evidence />
      </BusinessContext.Provider>
    </QueryClientProvider>,
  );
  try {
    fireEvent.click(await screen.findByRole("button", { name: /receipt.csv/ }));
    await waitFor(
      () => expect(screen.getByText("Fresh extracted receipt")).toBeVisible(),
      { timeout: 5000 },
    );
    expect(detailReads).toBe(2);
    await new Promise((resolve) => setTimeout(resolve, 3200));
    expect(detailReads).toBe(2);
  } finally {
    view.unmount();
    client.clear();
    get.mockRestore();
  }
}, 10000);
