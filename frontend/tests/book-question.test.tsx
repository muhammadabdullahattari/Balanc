import { expect, it } from "vitest";
import { resolveBookQuestion } from "../src/features/book-question";

it("retains the metric for an explicit period follow-up only", () => {
  expect(
    resolveBookQuestion(
      "What about November 2025?",
      "What was our net profit last month?",
    ),
  ).toBe("What was our net profit in November 2025");
  expect(resolveBookQuestion("And this month?", "")).toBe("And this month?");
  expect(
    resolveBookQuestion(
      "Why in November?",
      "Show me why expenses spiked last month",
    ),
  ).toBe("Why in November?");
});
it.each([
  ["What about revenue?", "show me revenue last month"],
  ["And expenses?", "show me expenses last month"],
  [
    "Compare that with the previous quarter",
    "compare net profit with previous quarter last month",
  ],
  [
    "Which accounts drove that?",
    "explain net profit change versus previous month last month",
  ],
  ["Show the top 5 expense categories", "show top expense accounts last month"],
  ["And for November 2025 revenue?", "show me revenue in November 2025"],
  ["For somewhere revenue?", "For somewhere revenue?"],
])("resolves %s only when context is available", (input, expected) => {
  expect(
    resolveBookQuestion(input, "What was our net profit last month?"),
  ).toBe(expected);
  expect(resolveBookQuestion(input, "")).toBe(input);
});
