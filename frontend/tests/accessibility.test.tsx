import { it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { QueryClientProvider } from "@tanstack/react-query";
import { Auth } from "../src/features/auth";
import { queryClient } from "../src/api/runtime";
it("sign-in has no detectable structural accessibility violations", async () => {
  const view = render(
    <QueryClientProvider client={queryClient}>
      <Auth />
    </QueryClientProvider>,
  );
  const results = await axe.run(view.container, {
    rules: { "color-contrast": { enabled: false } },
  });
  expect(
    results.violations.map((v) => ({
      id: v.id,
      targets: v.nodes.map((n) => n.target),
    })),
  ).toEqual([]);
  expect(
    screen.getByRole("button", { name: "Sign in", exact: true }),
  ).toHaveAccessibleName("Sign in");
  queryClient.clear();
}, 15000);
