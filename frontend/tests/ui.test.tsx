import { AccessContext } from "../src/features/context";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { money, Modal, Button, ErrorNotice } from "../src/components/ui";
import { ApiError } from "../src/api/runtime";
describe("accounting presentation", () => {
  it("preserves negative fractional and large exact amounts", () => {
    expect(money("-0.50", "USD")).toBe("USD -0.50");
    expect(money("999999999999999999.25", "USD")).toBe(
      "USD 999,999,999,999,999,999.25",
    );
    expect(money("100", "JPY")).toBe("JPY 100");
  });
  it("blocks repeat clicks while a command is pending", async () => {
    render(<Button busy>Approve & post</Button>);
    expect(screen.getByRole("button")).toBeDisabled();
  });
  it("exposes actionable errors and correlation references", () => {
    render(
      <ErrorNotice
        error={new ApiError("Period is locked", 409, "request-123")}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Period is locked");
    expect(screen.getByRole("alert")).toHaveTextContent("request-123");
  });
  it("gives a dialog a title and supports Escape", async () => {
    let closed = false;
    render(
      <Modal
        open
        title="Review invoice"
        description="Confirm the entry"
        onClose={() => {
          closed = true;
        }}
      >
        <Button>Approve</Button>
      </Modal>,
    );
    expect(
      screen.getByRole("dialog", { name: "Review invoice" }),
    ).toHaveAccessibleDescription("Confirm the entry");
    await userEvent.keyboard("{Escape}");
    expect(closed).toBe(true);
  });
});

it("requires verified posting permission and allows an accountant", () => {
  const view = render(
    <AccessContext.Provider
      value={{ role: "owner", can_post: false, can_invite: true }}
    >
      <Button permission="post">Post</Button>
    </AccessContext.Provider>,
  );
  expect(screen.getByRole("button", { name: "Post" })).toBeDisabled();
  view.rerender(
    <AccessContext.Provider
      value={{ role: "accountant", can_post: false, can_invite: false }}
    >
      <Button permission="post">Post</Button>
    </AccessContext.Provider>,
  );
  expect(screen.getByRole("button", { name: "Post" })).toBeEnabled();
});
