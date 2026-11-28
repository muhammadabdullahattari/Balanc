import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BusinessContext } from "../src/features/context";
import { SwipeReview } from "../src/features/swipe-review";
import { AutonomousOffice } from "../src/features/autonomous-ops";

vi.mock("../src/api/runtime", () => ({
  api: {
    GET: vi.fn(async () => ({
      data: { items: [], count: 0 },
      error: undefined,
    })),
    POST: vi.fn(async () => ({
      data: { imported_count: 2 },
      error: undefined,
    })),
  },
  result: vi.fn(async (p: Promise<{ data: unknown }>) => (await p).data),
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BusinessContext.Provider
          value={
            {
              id: "11111111-1111-1111-1111-111111111111",
              name: "Acme Test",
              role: "owner",
              currency: "USD",
              mfaEnabled: true,
            } as never
          }
        >
          {ui}
        </BusinessContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Next-Gen v2.0 UI (Swipe Review & Autonomous Office)", () => {
  it("renders Quick Transaction Review with OFX import panel", async () => {
    wrap(<SwipeReview />);
    expect(await screen.findByText("Quick Transaction Review")).toBeTruthy();
    expect(screen.getByText("Import Bank Statement (OFX / QFX)")).toBeTruthy();
  });

  it("renders Autonomous Finance Office with plain-language tabs", async () => {
    wrap(<AutonomousOffice />);
    expect(await screen.findByText("Autonomous Finance Office")).toBeTruthy();
    expect(screen.getByText("Schedules & Auto-Post")).toBeTruthy();
    expect(screen.getByText("Receipt Chasing")).toBeTruthy();
    expect(screen.getByText("Fraud & Owner Alerts")).toBeTruthy();
  });
});
