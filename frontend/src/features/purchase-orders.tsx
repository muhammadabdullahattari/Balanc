import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import {
  Badge,
  Button,
  ErrorNotice,
  Field,
  Loading,
  PageHeading,
  SectionTabs,
  Table,
  money,
} from "../components/ui";
import { today, useAccess, useBusiness } from "./context";

export function PurchaseOrdersAndCollections({
  initialSection = "po",
}: {
  initialSection?: "po" | "collections";
}) {
  const b = useBusiness();
  const access = useAccess();
  const path = { business_id: b.id };
  const [section, setSection] = useState<string>(initialSection);

  // PO creation state
  const [poNumber, setPoNumber] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [itemDesc, setItemDesc] = useState("");
  const [qty, setQty] = useState("1");
  const [unitPrice, setUnitPrice] = useState("");
  const [tolerance, setTolerance] = useState("5.00");

  // Bill variance check state
  const [checkPoNum, setCheckPoNum] = useState("");
  const [billRef, setBillRef] = useState("");
  const [invoicedAmt, setInvoicedAmt] = useState("");
  const [approvalNote, setApprovalNote] = useState("");

  // Collections state
  const [tone, setTone] = useState<"friendly" | "neutral" | "firm">("friendly");
  const [phrases, setPhrases] = useState("");
  const [sigName, setSigName] = useState(b.name);
  const [escDays, setEscDays] = useState("60");
  const [callNotes, setCallNotes] = useState<Record<string, string>>({});

  const poQuery = useQuery({
    queryKey: [...keys.business(b.id), "purchase-orders"],
    enabled: section === "po",
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/purchase-orders", {
          signal,
          params: { path },
        }),
      ),
  });

  const collectionsQuery = useQuery({
    queryKey: [...keys.business(b.id), "collections-overview"],
    enabled: section === "collections",
    retry: false,
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/collections", {
          signal,
          params: { path },
        }),
      ),
  });

  const refreshPo = () =>
    void queryClient.invalidateQueries({
      queryKey: [...keys.business(b.id), "purchase-orders"],
    });

  const refreshCollections = () =>
    void queryClient.invalidateQueries({
      queryKey: [...keys.business(b.id), "collections-overview"],
    });

  const createPo = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/purchase-orders", {
          params: { path },
          body: {
            number: poNumber,
            supplier_name: supplierName,
            issue_date: today(b.timezone),
            currency: b.currency,
            tax_total: "0.00",
            variance_tolerance_pct: tolerance,
            lines: [
              {
                description: itemDesc,
                quantity: qty,
                unit_price: unitPrice,
              },
            ],
            notes: "",
          },
        }),
      ),
    onSuccess: () => {
      setPoNumber("");
      setSupplierName("");
      setItemDesc("");
      setUnitPrice("");
      refreshPo();
    },
  });

  const matchBill = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/purchase-orders/match-bill",
          {
            params: { path },
            body: {
              po_number: checkPoNum,
              bill_reference: billRef,
              invoiced_amount: invoicedAmt,
            },
          },
        ),
      ),
    onSuccess: refreshPo,
  });

  const decideVariance = useMutation({
    mutationFn: ({
      matchId,
      decision,
    }: {
      matchId: string;
      decision: "approve" | "reject";
    }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/purchase-orders/matches/{match_id}/decide",
          {
            params: { path: { business_id: b.id, match_id: matchId } },
            body: {
              decision,
              reason: approvalNote.trim() || "Reviewed against supplier quote",
            },
          },
        ),
      ),
    onSuccess: () => {
      setApprovalNote("");
      refreshPo();
    },
  });

  const saveProfile = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/collections/profile", {
          params: { path },
          body: {
            tone,
            sample_phrases: phrases,
            signature_name: sigName,
            phone_escalation_days: Number(escDays) || 60,
          },
        }),
      ),
    onSuccess: refreshCollections,
  });

  const runLadder = useMutation({
    mutationFn: () =>
      result(
        api.POST("/api/v1/businesses/{business_id}/collections/run-ladder", {
          params: { path },
          body: { as_of: today(b.timezone) } as never,
        }),
      ),
    onSuccess: refreshCollections,
  });

  const completeCall = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes: string }) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/collections/tasks/{task_id}/complete",
          {
            params: { path: { business_id: b.id, task_id: taskId } },
            body: {
              outcome_notes: notes || "Spoke with customer billing team",
            },
          },
        ),
      ),
    onSuccess: refreshCollections,
  });

  const poData = poQuery.data as
    | {
        purchase_orders?: Array<{
          id: string;
          number: string;
          supplier_name: string;
          total: string;
          currency: string;
          variance_tolerance_pct: string;
          status: string;
        }>;
        matches?: Array<{
          id: string;
          po_number: string;
          supplier_name: string;
          bill_reference: string;
          po_amount: string;
          invoiced_amount: string;
          variance_pct: string;
          status: string;
          requires_owner_approval: boolean;
        }>;
      }
    | undefined;

  const colData = collectionsQuery.data as
    | {
        profile?: {
          tone: string;
          sample_phrases: string;
          signature_name: string;
          phone_escalation_days: number;
        };
        tasks?: Array<{
          id: string;
          invoice_number: string;
          customer_name: string;
          amount_due: string;
          currency: string;
          days_overdue: number;
          status: string;
          outcome_notes: string;
        }>;
      }
    | undefined;

  return (
    <>
      <PageHeading
        eyebrow="PURCHASING & COLLECTIONS"
        title={
          section === "po"
            ? "Purchase orders & price checks"
            : "Customer follow-up & call queue"
        }
        description="Match incoming supplier invoices against approved purchase orders before approval, and follow up on overdue customer invoices in your business's own tone."
      />

      <SectionTabs
        id="po-collections"
        label="Purchasing and collections"
        value={section}
        onChange={setSection}
        items={[
          ["po", "Purchase orders & variance checks"],
          ["collections", "Collection tone & 60-day call tasks"],
        ]}
      />

      <ErrorNotice
        error={
          createPo.error ||
          matchBill.error ||
          decideVariance.error ||
          saveProfile.error ||
          runLadder.error
        }
      />

      {section === "po" ? (
        <>
          <div className="dashboard-grid">
            <section className="panel settings-section">
              <h2>Create purchase order</h2>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  createPo.mutate();
                }}
              >
                <div className="form-grid">
                  <Field label="PO number">
                    <input
                      required
                      value={poNumber}
                      onChange={(e) => setPoNumber(e.target.value)}
                      placeholder="PO-104"
                    />
                  </Field>
                  <Field label="Supplier">
                    <input
                      required
                      value={supplierName}
                      onChange={(e) => setSupplierName(e.target.value)}
                      placeholder="Cloud Hardware Ltd"
                    />
                  </Field>
                  <Field label="Agreed item">
                    <input
                      required
                      value={itemDesc}
                      onChange={(e) => setItemDesc(e.target.value)}
                      placeholder="Server rack units"
                    />
                  </Field>
                  <Field label="Quantity">
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      required
                      value={qty}
                      onChange={(e) => setQty(e.target.value)}
                    />
                  </Field>
                  <Field label={`Agreed unit price (${b.currency})`}>
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      required
                      value={unitPrice}
                      onChange={(e) => setUnitPrice(e.target.value)}
                      placeholder="1000.00"
                    />
                  </Field>
                  <Field label="Allowed price variance (%)">
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="0.5"
                      required
                      value={tolerance}
                      onChange={(e) => setTolerance(e.target.value)}
                    />
                  </Field>
                </div>
                <div className="actions" style={{ marginTop: "0.85rem" }}>
                  <Button type="submit" busy={createPo.isPending}>
                    Approve purchase order
                  </Button>
                </div>
              </form>
            </section>

            <section className="panel settings-section">
              <h2>Check supplier bill against PO</h2>
              <p className="muted">
                Invoices exceeding the agreed purchase order price tolerance
                automatically pause and ask the workspace owner before approval.
              </p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  matchBill.mutate();
                }}
              >
                <div className="form-grid">
                  <Field label="PO number">
                    <input
                      required
                      value={checkPoNum}
                      onChange={(e) => setCheckPoNum(e.target.value)}
                      placeholder="PO-104"
                    />
                  </Field>
                  <Field label="Supplier invoice reference">
                    <input
                      required
                      value={billRef}
                      onChange={(e) => setBillRef(e.target.value)}
                      placeholder="BILL-882"
                    />
                  </Field>
                  <Field label={`Invoiced total (${b.currency})`}>
                    <input
                      type="number"
                      min="0.01"
                      step="0.01"
                      required
                      value={invoicedAmt}
                      onChange={(e) => setInvoicedAmt(e.target.value)}
                      placeholder="1080.00"
                    />
                  </Field>
                </div>
                <div className="actions" style={{ marginTop: "0.85rem" }}>
                  <Button
                    type="submit"
                    variant="secondary"
                    busy={matchBill.isPending}
                  >
                    Verify invoice price
                  </Button>
                </div>
              </form>
              {matchBill.data && (
                <div
                  className="notice"
                  role="status"
                  style={{ marginTop: "0.85rem" }}
                >
                  {String((matchBill.data as { message?: string }).message)}
                </div>
              )}
            </section>
          </div>

          {poQuery.isPending ? (
            <Loading />
          ) : (
            <section
              className="panel settings-section"
              style={{ marginTop: "1.25rem" }}
            >
              <h2>Purchase orders & price-variance approvals</h2>
              <Field label="Owner decision note (for flagged variances)">
                <input
                  value={approvalNote}
                  onChange={(e) => setApprovalNote(e.target.value)}
                  placeholder="Reason for approving or rejecting price difference"
                />
              </Field>
              <Table
                caption="Supplier invoice vs. purchase order checks"
                rows={poData?.matches || []}
                rowKey={(m) => m.id}
                emptyText="No supplier invoice checks recorded yet."
                columns={[
                  {
                    label: "PO",
                    render: (m) => <strong>{m.po_number}</strong>,
                  },
                  { label: "Supplier", render: (m) => m.supplier_name },
                  { label: "Invoice", render: (m) => m.bill_reference },
                  {
                    label: "PO vs. Invoiced",
                    render: (m) =>
                      `${money(m.po_amount, b.currency)} → ${money(m.invoiced_amount, b.currency)} (${m.variance_pct}%)`,
                  },
                  {
                    label: "Status",
                    render: (m) => (
                      <Badge
                        tone={
                          m.requires_owner_approval
                            ? "warning"
                            : m.status === "rejected"
                              ? "neutral"
                              : "good"
                        }
                      >
                        {m.status.replaceAll("_", " ")}
                      </Badge>
                    ),
                  },
                  {
                    label: "Owner decision",
                    render: (m) =>
                      m.requires_owner_approval && access?.role === "owner" ? (
                        <div className="p4-inline-actions">
                          <Button
                            type="button"
                            variant="secondary"
                            busy={decideVariance.isPending}
                            onClick={() =>
                              decideVariance.mutate({
                                matchId: m.id,
                                decision: "approve",
                              })
                            }
                          >
                            Approve variance
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            busy={decideVariance.isPending}
                            onClick={() =>
                              decideVariance.mutate({
                                matchId: m.id,
                                decision: "reject",
                              })
                            }
                          >
                            Reject
                          </Button>
                        </div>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
            </section>
          )}
        </>
      ) : (
        <div className="dashboard-grid">
          <section className="panel settings-section">
            <h2>Reminder tone & escalation ladder</h2>
            <p className="muted">
              Customize how overdue reminders sound, and automatically escalate
              long-overdue invoices into personal phone-call tasks instead of
              sending endless automated messages.
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                saveProfile.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Default business tone">
                  <select
                    value={tone}
                    onChange={(e) =>
                      setTone(e.target.value as "friendly" | "neutral" | "firm")
                    }
                  >
                    <option value="friendly">Warm & polite</option>
                    <option value="neutral">Professional & direct</option>
                    <option value="firm">Firm & urgent</option>
                  </select>
                </Field>
                <Field label="Escalate to phone call after (days late)">
                  <input
                    type="number"
                    min="15"
                    max="365"
                    required
                    value={escDays}
                    onChange={(e) => setEscDays(e.target.value)}
                  />
                </Field>
                <Field label="Sign-off name">
                  <input
                    value={sigName}
                    onChange={(e) => setSigName(e.target.value)}
                  />
                </Field>
                <Field label="Custom closing phrase (optional)">
                  <input
                    value={phrases}
                    onChange={(e) => setPhrases(e.target.value)}
                    placeholder="Thank you for your partnership."
                  />
                </Field>
              </div>
              <div
                className="p4-inline-actions"
                style={{ marginTop: "0.85rem" }}
              >
                <Button type="submit" busy={saveProfile.isPending}>
                  Save tone settings
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  busy={runLadder.isPending}
                  onClick={() => runLadder.mutate()}
                >
                  Run overdue follow-up check
                </Button>
              </div>
            </form>
          </section>

          <section className="panel settings-section">
            <h2>60+ day phone-call task queue</h2>
            <Table
              caption="Overdue invoices escalated to personal phone calls"
              rows={colData?.tasks || []}
              rowKey={(t) => t.id}
              emptyText="No invoices currently require an escalated phone call."
              columns={[
                { label: "Customer", render: (t) => t.customer_name },
                { label: "Invoice", render: (t) => t.invoice_number },
                {
                  label: "Amount & age",
                  render: (t) =>
                    `${money(t.amount_due, t.currency)} · ${t.days_overdue}d late`,
                },
                {
                  label: "Status",
                  render: (t) => (
                    <Badge tone={t.status === "completed" ? "good" : "warning"}>
                      {t.status}
                    </Badge>
                  ),
                },
                {
                  label: "Log call",
                  render: (t) =>
                    t.status === "open" ? (
                      <div className="p4-inline-actions">
                        <input
                          placeholder="Call outcome"
                          value={callNotes[t.id] || ""}
                          onChange={(e) =>
                            setCallNotes((prev) => ({
                              ...prev,
                              [t.id]: e.target.value,
                            }))
                          }
                        />
                        <Button
                          type="button"
                          variant="secondary"
                          busy={completeCall.isPending}
                          onClick={() =>
                            completeCall.mutate({
                              taskId: t.id,
                              notes:
                                callNotes[t.id] || "Confirmed payment promise",
                            })
                          }
                        >
                          Complete
                        </Button>
                      </div>
                    ) : (
                      t.outcome_notes || "Completed"
                    ),
                },
              ]}
            />
          </section>
        </div>
      )}
    </>
  );
}
