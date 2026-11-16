import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "../src/api/runtime";
import { ClosePackages } from "../src/features/close-packages";
import { ExportDisposal } from "../src/features/export-disposal";
import {
  AccessContext,
  BusinessContext,
  type Business,
} from "../src/features/context";

afterEach(() => vi.restoreAllMocks());
const reply = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function mount(node: React.ReactNode) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <BusinessContext.Provider
        value={{ id: "tenant", currency: "USD" } as Business}
      >
        <AccessContext.Provider
          value={{ role: "accountant", can_invite: false, can_post: false }}
        >
          {node}
        </AccessContext.Provider>
      </BusinessContext.Provider>
    </QueryClientProvider>,
  );
}

it("prepares, opens and signs a package when the server grants review access", async () => {
  const item = {
    id: "package",
    start: "2026-01-01",
    end: "2026-01-31",
    locked: true,
    prepared_by: "another member",
    prepared_at: "2026-02-01T00:00:00Z",
    readiness: {},
    tasks: [],
  };
  const get = vi
    .spyOn(api, "GET")
    .mockResolvedValueOnce(reply({ items: [] }))
    .mockResolvedValueOnce(
      reply({ items: [{ package: item, review: null, can_sign: true }] }),
    )
    .mockResolvedValue(
      reply({
        items: [
          {
            package: item,
            review: {
              reviewed_by: "reviewer",
              reviewed_at: "2026-02-02T00:00:00Z",
            },
            can_sign: false,
          },
        ],
      }),
    );
  const post = vi.spyOn(api, "POST").mockResolvedValue(reply(item));
  mount(<ClosePackages periodId="period" />);
  await screen.findByText("No workpapers saved for this period.");
  fireEvent.click(
    screen.getByRole("button", { name: "Prepare workpaper package" }),
  );
  fireEvent.click(await screen.findByText(/Awaiting review/));
  fireEvent.click(
    screen.getByRole("button", { name: "Record independent sign-off" }),
  );
  expect(
    await screen.findByText("Independent sign-off recorded."),
  ).toBeVisible();
  expect(post.mock.calls[1]?.[0]).toContain("/review");
  expect(get).toHaveBeenCalledTimes(3);
});

it("requires a checked confirmation before deleting eligible copies", async () => {
  vi.spyOn(api, "GET")
    .mockResolvedValueOnce(
      reply({
        execution_enabled: true,
        items: [
          {
            id: "copy",
            expires_at: "2026-01-01T00:00:00Z",
            decision: "eligible",
          },
        ],
      }),
    )
    .mockResolvedValue(reply({ execution_enabled: true, items: [] }));
  const post = vi
    .spyOn(api, "POST")
    .mockResolvedValue(reply({ deleted: ["copy"] }));
  mount(<ExportDisposal />);
  fireEvent.click(
    screen.getByRole("button", { name: "Preview export disposal" }),
  );
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "Select export copy" }),
  );
  const button = screen.getByRole("button", {
    name: "Delete selected exports",
  });
  expect(button).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: /Confirm permanent removal/ }),
  );
  fireEvent.click(button);
  expect(
    await screen.findByText("Deleted 1 managed export copies."),
  ).toBeVisible();
  expect(post.mock.calls[0]?.[1]).toMatchObject({ body: { ids: ["copy"] } });
});
