import { afterEach, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "../src/api/runtime";
import { RetentionControls } from "../src/features/retention";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";

const clients: QueryClient[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  clients.forEach((c) => c.clear());
  clients.length = 0;
});
function mount(enabled = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <BusinessContext.Provider
        value={
          {
            id: "workspace",
            timezone: "UTC",
            p2_retention_available: enabled,
          } as Business
        }
      >
        <AccessContext.Provider
          value={{ role: "owner", can_invite: true, can_post: true }}
        >
          <RetentionControls />
        </AccessContext.Provider>
      </BusinessContext.Provider>
    </QueryClientProvider>,
  );
}
const reply = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});

it("does not request controls for a workspace outside the rollout", () => {
  const get = vi.spyOn(api, "GET");
  mount(false);
  expect(get).not.toHaveBeenCalled();
  expect(
    screen.queryByText("Retention and legal holds"),
  ).not.toBeInTheDocument();
});

it("saves the current policy version and explains legal hold precedence", async () => {
  vi.spyOn(api, "GET").mockResolvedValue(
    reply({
      default_years: 10,
      policies: [
        {
          id: "policy",
          category: "journals",
          jurisdiction: "US",
          years: 10,
          version: 3,
          reference: "Previous policy",
        },
      ],
      holds: [],
    }),
  );
  const post = vi
    .spyOn(api, "POST")
    .mockResolvedValue(reply({ id: "new-policy", version: 4 }));
  mount();
  await screen.findByText("Previous policy");
  fireEvent.change(screen.getByLabelText("Approved policy reference"), {
    target: { value: "Approved US policy" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save policy version" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/v1/businesses/{business_id}/retention/policies",
      expect.objectContaining({
        body: {
          category: "journals",
          jurisdiction: "US",
          years: 10,
          expected_version: 3,
          reference: "Approved US policy",
        },
      }),
    ),
  );
  post.mockResolvedValue(
    reply({
      retain_until: "2036-01-01",
      active_hold_ids: ["hold"],
      retention_elapsed: true,
      eligible_for_disposal_review: false,
      deletion_authorized: false,
    }),
  );
  fireEvent.change(screen.getByLabelText("Record date"), {
    target: { value: "2026-01-01" },
  });
  fireEvent.change(screen.getByLabelText("Assessment date"), {
    target: { value: "2037-01-01" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Check eligibility" }));
  expect(
    await screen.findByText(/An active legal hold prevents disposal review/),
  ).toBeVisible();
});
