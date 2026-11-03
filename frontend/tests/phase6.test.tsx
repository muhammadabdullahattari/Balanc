import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  Button,
  Empty,
  Loading,
  Field,
  NextPage,
  Table,
  ErrorNotice,
} from "../src/components/ui";
import { AccessContext } from "../src/features/context";
import { ApiError, result } from "../src/api/runtime";

describe("Phase 6 independent UI states", () => {
  it("announces loading and provides an empty-state recovery action", async () => {
    const add = vi.fn();
    const view = render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    view.rerender(
      <Empty
        title="No invoices"
        action={<Button onClick={add}>Create invoice</Button>}
      >
        Choose a customer to begin.
      </Empty>,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create invoice" }),
    );
    expect(add).toHaveBeenCalledOnce();
  });
  it("labels fields and supports keyboard entry and filtering controls", async () => {
    render(
      <Field label="Document kind">
        <select defaultValue="invoice">
          <option value="invoice">Invoices</option>
          <option value="bill">Bills</option>
        </select>
      </Field>,
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Document kind"),
      "bill",
    );
    expect(screen.getByLabelText("Document kind")).toHaveValue("bill");
  });
  it("prevents navigation past the last page and preserves reset", async () => {
    const next = vi.fn(),
      reset = vi.fn();
    const view = render(
      <NextPage cursor={null} onNext={next} onReset={reset} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Next page/ }));
    expect(next).not.toHaveBeenCalled();
    view.rerender(<NextPage cursor="opaque" onNext={next} onReset={reset} />);
    await userEvent.click(screen.getByRole("button", { name: /Next page/ }));
    await userEvent.click(screen.getByRole("button", { name: "First page" }));
    expect(next).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
  });
  it("exposes table captions, column headings and exact server values", () => {
    render(
      <Table
        caption="Cash trend data"
        rows={[{ id: "a", value: "999999999999.25" }]}
        rowKey={(r) => r.id}
        columns={[{ label: "Amount", render: (r) => r.value }]}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Cash trend data" }),
    ).toHaveAttribute("tabindex", "0");
    expect(
      screen.getByRole("columnheader", { name: "Amount" }),
    ).toHaveAttribute("scope", "col");
    expect(screen.getByRole("cell")).toHaveTextContent("999999999999.25");
  });
  it.each([401, 403, 409, 422, 429, 503])(
    "renders HTTP %s with an explicit recovery action",
    async (status) => {
      const retry = vi.fn();
      render(
        <ErrorNotice
          error={new ApiError("Review required", status, "safe-reference")}
          retry={retry}
        />,
      );
      expect(screen.getByRole("alert")).toHaveTextContent("safe-reference");
      await userEvent.click(screen.getByRole("button", { name: "Try again" }));
      expect(retry).toHaveBeenCalledOnce();
    },
  );
  it("does not grant posting or invitations when membership is absent", () => {
    render(
      <>
        <Button permission="post">Post</Button>
        <Button permission="invite">Invite</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Post" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Invite" })).toBeDisabled();
  });
  it("keeps explicitly disabled owner controls disabled", () => {
    render(
      <AccessContext.Provider
        value={{ role: "owner", can_post: true, can_invite: true }}
      >
        <Button disabled permission="post">
          Post
        </Button>
      </AccessContext.Provider>,
    );
    expect(screen.getByRole("button", { name: "Post" })).toBeDisabled();
  });
  it("normalizes offline and timeout failures without claiming success", async () => {
    await expect(
      result(Promise.reject(new TypeError("network"))),
    ).rejects.toThrow("reach the server");
    await expect(
      result(Promise.reject(new DOMException("deadline", "TimeoutError"))),
    ).rejects.toThrow("timed out");
  });
});

it("preserves request reference headers when the gateway error body has no reference", async () => {
  try {
    await result(
      Promise.resolve({
        error: { error: { message: "Unavailable" } },
        response: new Response(null, {
          status: 503,
          headers: { "X-Request-ID": "gateway-reference" },
        }),
      }),
    );
    throw new Error("Expected failure");
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).requestId).toBe("gateway-reference");
  }
});

it("keeps select accessible names stable after choosing an option and links guidance separately", async () => {
  render(
    <Field label="Customer" hint="Choose who owes the amount">
      <select defaultValue="">
        <option value="">Choose a contact</option>
        <option value="customer">Synthetic customer</option>
      </select>
    </Field>,
  );
  const control = screen.getByRole("combobox", {
    name: "Customer",
    exact: true,
  });
  expect(control).toHaveAccessibleDescription("Choose who owes the amount");
  await userEvent.selectOptions(control, "customer");
  expect(
    screen.getByRole("combobox", { name: "Customer", exact: true }),
  ).toHaveValue("customer");
});
