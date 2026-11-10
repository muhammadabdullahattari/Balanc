import { LookupMore } from "./lookup-more";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Button,
  Empty,
  ErrorNotice,
  Field,
  Modal,
  PageHeading,
  Table,
  money,
} from "../components/ui";
import { today, useBusiness } from "./context";
import type { components } from "../api/generated/schema";
export function Reconciliation() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [command, setCommand] =
    useState<components["schemas"]["StatementCreate"]>();
  const [outstanding, setOutstanding] = useState<
    { journal_id: string; position: number }[]
  >([]);
  const accounts = useQuery({
    queryKey: [...keys.business(b.id), "accounts"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/accounts", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const evidence = useQuery({
    queryKey: [...keys.business(b.id), "reconciliation-evidence"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/evidence", {
          signal,
          params: { path, query: { limit: 200 } },
        }),
      ),
  });
  const prepare = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const v = new FormData(form);
      const current = await result(
        api.GET("/api/v1/businesses/{business_id}", { params: { path } }),
      );
      setCommand({
        idempotency_key: crypto.randomUUID(),
        expected_version: current.version,
        account_id: String(v.get("account")),
        as_of: String(v.get("date")),
        closing_balance: String(v.get("balance")),
        evidence_id: String(v.get("evidence")),
        outstanding,
      });
    },
  });
  const post = useMutation({
    mutationFn: (body: components["schemas"]["StatementCreate"]) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/statements/sign-off", {
          params: { path },
          body,
        }),
      ),
    onSuccess: () => {
      setCommand(undefined);
      void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
    },
  });
  return (
    <>
      <LookupMore resource="accounts" />
      <LookupMore resource="parties" />
      <PageHeading
        eyebrow="BANK RECONCILIATION"
        title="Bank reconciliation"
        description="Tie your closing statement to the ledger, with evidence for every outstanding item."
      />
      <ErrorNotice error={accounts.error || evidence.error || prepare.error} />
      <div className="dashboard-grid">
        <section className="panel settings-section">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              post.reset();
              prepare.mutate(e.currentTarget);
            }}
          >
            <div className="form-grid">
              <Field label="Bank or card account">
                <select required name="account">
                  <option value="">Choose account</option>
                  {accounts.data?.items
                    .filter(
                      (a) =>
                        a.active &&
                        !a.control &&
                        ["asset", "liability"].includes(a.kind),
                    )
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Statement closing date">
                <input
                  type="date"
                  name="date"
                  required
                  defaultValue={today(b.timezone)}
                />
              </Field>
            </div>
            <Field label={`Statement closing balance (${b.currency})`}>
              <input
                name="balance"
                inputMode="decimal"
                required
                pattern="-?[0-9]+(\.[0-9]{1,6})?"
              />
            </Field>
            <Field label="Supporting statement document">
              <select name="evidence" required>
                <option value="">Choose uploaded evidence</option>
                {evidence.data?.items
                  .filter((e) =>
                    ["clean", "extracted", "unsupported"].includes(e.status),
                  )
                  .map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.filename} · {e.status}
                    </option>
                  ))}
              </select>
            </Field>
            <fieldset>
              <legend>Outstanding ledger items</legend>
              <p className="muted">
                Include only transactions already posted but not yet on this
                statement. Find their journal reference and line number in the
                general ledger.
              </p>
              {outstanding.map((item, i) => (
                <div className="form-grid" key={i}>
                  <Field label={`Journal reference ${i + 1}`}>
                    <input
                      required
                      value={item.journal_id}
                      onChange={(e) =>
                        setOutstanding((items) =>
                          items.map((v, j) =>
                            j === i ? { ...v, journal_id: e.target.value } : v,
                          ),
                        )
                      }
                    />
                  </Field>
                  <Field label="Line number">
                    <input
                      type="number"
                      min={1}
                      max={200}
                      required
                      value={item.position}
                      onChange={(e) =>
                        setOutstanding((items) =>
                          items.map((v, j) =>
                            j === i
                              ? { ...v, position: Number(e.target.value) }
                              : v,
                          ),
                        )
                      }
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() =>
                      setOutstanding((items) => items.filter((_, j) => i !== j))
                    }
                  >
                    Remove item {i + 1}
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="secondary"
                onClick={() =>
                  setOutstanding((items) => [
                    ...items,
                    { journal_id: "", position: 1 },
                  ])
                }
              >
                Add outstanding item
              </Button>
            </fieldset>
            <Button busy={prepare.isPending}>Review sign-off</Button>
          </form>
        </section>
        <section className="panel focus-panel">
          <span className="eyebrow">A CONTROL, NOT A GUESS</span>
          <h2>A clear reconciliation trail.</h2>
          <p className="muted">
            The server checks your statement against the complete ledger. An
            unexplained difference blocks sign-off.
          </p>
          <ol className="muted">
            <li>Review imported bank activity.</li>
            <li>Upload the closing statement in Documents.</li>
            <li>Identify genuine timing differences.</li>
            <li>Have an authorized accountant sign off.</li>
          </ol>
        </section>
      </div>
      {post.data && (
        <section className="panel settings-section" role="status">
          <h2>Statement signed off.</h2>
          <dl className="review-summary">
            <dt>Ledger balance</dt>
            <dd>{money(post.data.ledger_balance, b.currency)}</dd>
            <dt>Statement balance</dt>
            <dd>{money(post.data.statement_balance, b.currency)}</dd>
            <dt>Outstanding</dt>
            <dd>{money(post.data.outstanding, b.currency)}</dd>
            <dt>Unexplained difference</dt>
            <dd>{money(post.data.unexplained_difference, b.currency)}</dd>
          </dl>
        </section>
      )}
      <Modal
        open={!!command}
        onClose={() => {
          if (!post.isPending) setCommand(undefined);
        }}
        title="Confirm statement sign-off"
        description="This records an accountant-approved reconciliation. It does not create a balancing entry."
      >
        <ErrorNotice error={post.error} />
        {command && (
          <>
            <dl className="review-summary">
              <dt>Closing date</dt>
              <dd>{command.as_of}</dd>
              <dt>Statement balance</dt>
              <dd>{money(command.closing_balance, b.currency)}</dd>
              <dt>Outstanding items</dt>
              <dd>{outstanding.length}</dd>
            </dl>
            <Button
              permission="post"
              busy={post.isPending}
              onClick={() => post.mutate(command)}
            >
              {post.isError ? "Retry same sign-off" : "Approve sign-off"}
            </Button>
          </>
        )}
      </Modal>
    </>
  );
}
