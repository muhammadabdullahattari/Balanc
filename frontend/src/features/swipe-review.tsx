import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, result } from "../api/runtime";
import { Badge, Button, ErrorNotice, Loading } from "../components/ui";
import { useBusiness } from "./context";

const QUICK_CATEGORIES = [
  "Office Supplies",
  "Software & Subscriptions",
  "Cloud & Hosting",
  "Meals & Entertainment",
  "Travel & Lodging",
  "Professional Services",
  "Marketing & Advertising",
  "Repairs & Maintenance",
];

type QueueItem = {
  bank_row_id: string;
  posted_date: string;
  raw_description: string;
  amount: string;
  currency: string;
  merchant_name: string;
  suggested_category: string;
  confidence: string;
  logo_url?: string | null;
  journal_preview?: {
    balanced: boolean;
    total_debit: string;
    total_credit: string;
    lines: Array<{
      account_name: string;
      debit: string;
      credit: string;
      memo: string;
    }>;
  };
};

export function SwipeReview() {
  const { id } = useBusiness();
  const [cursor, setCursor] = useState(0);
  const [selectedCategory, setSelectedCategory] = useState("");
  const [note, setNote] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [ofxText, setOfxText] = useState("");
  const [ofxResult, setOfxResult] = useState<string | null>(null);

  const queueQuery = useQuery({
    queryKey: ["review-queue", id],
    queryFn: async () =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/review-queue", {
          params: { path: { business_id: id } },
        }),
      )) as { items: QueueItem[]; count: number },
  });

  const decide = useMutation({
    mutationFn: async (input: {
      bankRowId: string;
      action: "approve" | "reclassify" | "skip";
      category?: string;
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/review-queue/{bank_row_id}/decision",
          {
            params: { path: { business_id: id, bank_row_id: input.bankRowId } },
            body: {
              action: input.action,
              category: input.category ?? null,
              note,
            },
          },
        ),
      ),
    onSuccess: (_data, vars) => {
      setStatusMessage(
        vars.action === "approve"
          ? "Transaction approved and categorized."
          : vars.action === "reclassify"
            ? `Updated category to ${vars.category ?? "selected category"}.`
            : "Skipped for later review.",
      );
      setSelectedCategory("");
      setNote("");
      setCursor((c) => c + 1);
    },
  });

  const importOfx = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/bank-accounts/{account_id}/imports/ofx",
          {
            params: {
              path: {
                business_id: id,
                account_id: id,
              },
            },
            body: { ofx_content: ofxText },
          },
        ),
      )) as { imported_count: number },
    onSuccess: (data) => {
      setOfxResult(
        `Imported ${data.imported_count} bank transactions with clean merchant names.`,
      );
      setOfxText("");
      void queueQuery.refetch();
    },
  });

  if (queueQuery.isLoading) {
    return <Loading />;
  }

  const items = queueQuery.data?.items ?? [];
  const current = items[cursor] ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Quick Transaction Review</h1>
          <p className="text-sm text-stone-600">
            Approve clean merchant categories and double-entry previews in one
            tap.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone="good">
            {Math.max(0, items.length - cursor)} remaining
          </Badge>
          <Link
            to="/capture"
            className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm font-medium hover:bg-stone-100"
          >
            Snap receipt
          </Link>
        </div>
      </div>

      {queueQuery.error ? <ErrorNotice error={queueQuery.error} /> : null}
      {decide.error ? <ErrorNotice error={decide.error} /> : null}
      {statusMessage ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          {statusMessage}
        </div>
      ) : null}

      {current ? (
        <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
          <h2 className="text-lg font-semibold">{current.merchant_name}</h2>
          <p className="mb-3 text-xs text-stone-500">
            Posted {current.posted_date}
          </p>
          <div className="space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-2xl font-bold">
                {current.currency} {current.amount}
              </span>
              <Badge tone="good">
                Suggested: {current.suggested_category} (
                {Math.round(Number(current.confidence) * 100)}%)
              </Badge>
            </div>
            <p className="text-xs text-stone-500">
              Statement line: {current.raw_description}
            </p>

            {current.journal_preview ? (
              <div className="rounded-lg border border-stone-200 bg-stone-50 p-3 text-sm">
                <p className="mb-2 font-medium">
                  Balanced Double-Entry Preview
                </p>
                <div className="space-y-1">
                  {current.journal_preview.lines.map((ln, idx) => (
                    <div key={idx} className="flex justify-between gap-2">
                      <span>{ln.account_name}</span>
                      <span className="font-mono text-xs">
                        Dr {ln.debit} / Cr {ln.credit}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-stone-500">
                Or pick another category
              </p>
              <div className="flex flex-wrap gap-2">
                {QUICK_CATEGORIES.map((cat) => (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setSelectedCategory(cat)}
                    className={`rounded-full border px-3 py-1 text-xs font-medium ${
                      selectedCategory === cat
                        ? "border-stone-900 bg-stone-900 text-white"
                        : "border-stone-300 bg-white text-stone-800 hover:bg-stone-100"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>

            <label className="block text-sm">
              <span className="mb-1 block text-xs font-medium text-stone-600">
                Optional note for your accountant
              </span>
              <input
                type="text"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="e.g. Client lunch in Austin"
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
              />
            </label>

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={() =>
                  decide.mutate({
                    bankRowId: current.bank_row_id,
                    action: selectedCategory ? "reclassify" : "approve",
                    category: selectedCategory || current.suggested_category,
                  })
                }
                disabled={decide.isPending}
              >
                {selectedCategory
                  ? `Save as ${selectedCategory}`
                  : "Approve & Categorize"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  decide.mutate({
                    bankRowId: current.bank_row_id,
                    action: "skip",
                  })
                }
                disabled={decide.isPending}
              >
                Skip for later
              </Button>
            </div>
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
          <h2 className="mb-1 text-base font-semibold">All caught up</h2>
          <p className="text-sm text-stone-600">
            There are no uncategorized bank transactions waiting for your
            review.
          </p>
        </section>
      )}

      <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
        <h2 className="mb-2 text-base font-semibold">
          Import Bank Statement (OFX / QFX)
        </h2>
        <div className="space-y-3">
          <p className="text-sm text-stone-600">
            Paste or drop bank statement content in OFX or QFX format to parse
            transactions and clean merchant names automatically.
          </p>
          <textarea
            rows={4}
            value={ofxText}
            onChange={(e) => setOfxText(e.target.value)}
            placeholder="Paste OFX or QFX statement content..."
            className="w-full rounded-lg border border-stone-300 p-2 font-mono text-xs"
          />
          {importOfx.error ? <ErrorNotice error={importOfx.error} /> : null}
          {ofxResult ? (
            <p className="text-sm text-emerald-700">{ofxResult}</p>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            disabled={importOfx.isPending || ofxText.trim().length < 10}
            onClick={() => importOfx.mutate()}
          >
            Import OFX Statement
          </Button>
        </div>
      </section>
    </div>
  );
}
