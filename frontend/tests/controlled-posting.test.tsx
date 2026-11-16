import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../src/api/runtime";
import { ControlledPosting } from "../src/features/controlled-posting";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";

afterEach(() => vi.restoreAllMocks());

it("posts only after review and sends the exact dry-run scope and fingerprint", async () => {
  const reply = (data: unknown) => ({
    data,
    response: new Response(null, { status: 200 }),
  });
  const hash = "a".repeat(64);
  vi.spyOn(api, "GET").mockResolvedValue(reply({ paused: false }));
  const post = vi
    .spyOn(api, "POST")
    .mockResolvedValueOnce(
      reply({
        status: "dry_run",
        preview_hash: hash,
        candidates: [
          {
            id: "row",
            date: "2026-11-12",
            description: "Hosting",
            amount: "-75",
          },
        ],
        flagged_amount_count: 0,
        flagged_exclusion_count: 0,
      }),
    )
    .mockResolvedValueOnce(reply({ status: "posted", posted_count: 1 }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BusinessContext.Provider
        value={{ id: "tenant", currency: "USD" } as Business}
      >
        <AccessContext.Provider
          value={{ role: "accountant", can_post: true, can_invite: false }}
        >
          <ControlledPosting
            rules={[
              { id: "rule", name: "Hosting rule", active: true, version: 4 },
            ]}
            onClose={() => {}}
          />
        </AccessContext.Provider>
      </BusinessContext.Provider>
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: "Pause posting" });
  fireEvent.change(screen.getByLabelText("Approved bank rule"), {
    target: { value: "rule" },
  });
  fireEvent.change(screen.getByLabelText("Start date"), {
    target: { value: "2026-11-01" },
  });
  fireEvent.change(screen.getByLabelText("End date"), {
    target: { value: "2026-11-30" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run dry run" }));
  const confirm = await screen.findByRole("button", {
    name: "Post 1 transactions",
  });
  expect(confirm).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(confirm);
  expect(
    await screen.findByText("Posted 1 transactions to the general ledger."),
  ).toBeVisible();
  expect(post).toHaveBeenNthCalledWith(
    2,
    expect.stringContaining("/controlled-posting"),
    {
      params: {
        path: { business_id: "tenant", rule_id: "rule" },
        query: {
          start: "2026-11-01",
          end: "2026-11-30",
          max_amount: "500",
          expected_rule_version: 4,
          dry_run: false,
          expected_preview_hash: hash,
        },
      },
    },
  );
});
