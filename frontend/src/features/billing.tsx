import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CreditCard,
  Download,
  ExternalLink,
  FileText,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { api, keys, queryClient, result } from "../api/runtime";
import { Badge, Button, ErrorNotice, Modal } from "../components/ui";
import { useAccess, useBusiness } from "./context";

type PlanKey = "starter" | "growth" | "enterprise";

const PLAN_ORDER: Record<PlanKey, number> = {
  starter: 1,
  growth: 2,
  enterprise: 3,
};

function formatCurrency(cents: number | null | undefined, currency = "usd") {
  if (cents == null) return "—";
  const code = currency.toUpperCase();
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: code,
      maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
    }).format(cents / 100);
  } catch {
    return `${code} ${(cents / 100).toFixed(2)}`;
  }
}

function formatUnixDate(ts: number | null | undefined) {
  if (!ts) return "—";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(ts * 1000));
}

function statusMeta(status: string, cancelAtPeriodEnd: boolean) {
  if (status === "active" && cancelAtPeriodEnd) {
    return { label: "Canceling at period end", tone: "warning" as const };
  }
  if (status === "active" || status === "trialing") {
    return {
      label: status === "trialing" ? "Trialing" : "Active",
      tone: "success" as const,
    };
  }
  if (status === "past_due") {
    return { label: "Past due", tone: "danger" as const };
  }
  if (status === "canceled" || status === "expired") {
    return { label: "Canceled", tone: "danger" as const };
  }
  return { label: "No active plan", tone: "muted" as const };
}

export function BillingSettings() {
  const b = useBusiness();
  const access = useAccess();
  const isOwner = access?.role === "owner";
  const path = { business_id: b.id };

  const [cancelModalOpen, setCancelModalOpen] = useState(false);
  const [returnNotice, setReturnNotice] = useState<{
    kind: "success" | "info";
    text: string;
  } | null>(null);
  const reconciledReturn = useRef(false);

  const plansQuery = useQuery({
    queryKey: ["billing", "plans"],
    queryFn: ({ signal }) =>
      result(api.GET("/api/v1/billing/plans", { signal })),
  });

  const subQuery = useQuery({
    queryKey: [...keys.business(b.id), "billing", "subscription"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/billing/subscription", {
          signal,
          params: { path },
        }),
      ),
  });

  const invoicesQuery = useQuery({
    queryKey: [...keys.business(b.id), "billing", "invoices"],
    enabled: Boolean(subQuery.data?.customer),
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/billing/invoices", {
          signal,
          params: { path },
        }),
      ),
  });

  const refreshAllBilling = () => {
    void queryClient.invalidateQueries({
      queryKey: [...keys.business(b.id), "billing", "subscription"],
    });
    void queryClient.invalidateQueries({
      queryKey: [...keys.business(b.id), "billing", "invoices"],
    });
    void queryClient.invalidateQueries({ queryKey: keys.business(b.id) });
  };

  const reconcile = useMutation({
    mutationFn: (sessionId?: string) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/billing/reconcile", {
          params: {
            path,
            query: sessionId ? { session_id: sessionId } : undefined,
          },
        }),
      ),
    onSuccess: () => {
      refreshAllBilling();
    },
  });

  useEffect(() => {
    if (reconciledReturn.current) return;
    const params = new URLSearchParams(window.location.search);
    const billingResult = params.get("billing");
    const sessionId = params.get("session_id") || undefined;
    if (billingResult === "success") {
      reconciledReturn.current = true;
      setReturnNotice({
        kind: "success",
        text: "Payment confirmed! Syncing your yearly subscription, unlocking all workspace features, and emailing your receipt.",
      });
      if (isOwner) {
        reconcile.mutate(sessionId);
      } else {
        refreshAllBilling();
      }
      params.delete("billing");
      params.delete("session_id");
      params.delete("business_id");
      const clean = params.toString();
      window.history.replaceState(
        {},
        "",
        window.location.pathname + (clean ? `?${clean}` : ""),
      );
    } else if (billingResult === "canceled") {
      reconciledReturn.current = true;
      setReturnNotice({
        kind: "info",
        text: "Stripe Checkout was canceled. No charge was made — choose a yearly plan anytime below.",
      });
      params.delete("billing");
      params.delete("business_id");
      const clean = params.toString();
      window.history.replaceState(
        {},
        "",
        window.location.pathname + (clean ? `?${clean}` : ""),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOwner, b.id]);

  const checkout = useMutation({
    mutationFn: (plan: PlanKey) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/billing/checkout", {
          params: { path, query: { plan } },
        }),
      ),
    onSuccess: (data) => {
      if (data && typeof data === "object" && "url" in data && data.url) {
        window.location.assign(String(data.url));
      }
    },
  });

  const changePlan = useMutation({
    mutationFn: (plan: PlanKey) =>
      result(
        api.POST("/api/v1/businesses/{business_id}/billing/subscription/plan", {
          params: { path, query: { new_plan: plan } },
        }),
      ),
    onSuccess: (_data, plan) => {
      refreshAllBilling();
      setReturnNotice({
        kind: "success",
        text: `Subscription switched to the ${plan.charAt(0).toUpperCase() + plan.slice(1)} yearly plan. A confirmation email has been sent.`,
      });
    },
  });

  const cancelSub = useMutation({
    mutationFn: (immediate: boolean) =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/billing/subscription/cancel",
          {
            params: { path, query: { immediate } },
          },
        ),
      ),
    onSuccess: (_data, immediate) => {
      setCancelModalOpen(false);
      refreshAllBilling();
      setReturnNotice({
        kind: "info",
        text: immediate
          ? "Your subscription has been canceled immediately. A confirmation email has been sent; your records remain readable and exportable."
          : "Cancellation scheduled for the end of your current yearly period. Full access continues until then, and a confirmation email has been sent.",
      });
    },
  });

  const resumeSub = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/billing/subscription/resume",
          {
            params: { path },
          },
        ),
      ),
    onSuccess: () => {
      refreshAllBilling();
      setReturnNotice({
        kind: "success",
        text: "Your yearly subscription has been reactivated and will renew normally. A confirmation email has been sent.",
      });
    },
  });

  const sub = subQuery.data;
  const status = sub?.status || "unlinked";
  const currentPlan = (sub?.plan || "starter") as PlanKey;
  const paidAccess = Boolean(sub?.paid_access);
  const cancelAtPeriodEnd = Boolean(sub?.cancel_at_period_end);
  const hasActiveSub =
    Boolean(sub?.subscription_id) &&
    ["active", "trialing", "past_due"].includes(status);
  const graceUntil = sub?.grace_until;
  const isGraceActive = Boolean(graceUntil && Date.now() < graceUntil * 1000);
  const badge = statusMeta(status, cancelAtPeriodEnd);
  const plans = plansQuery.data?.plans || [];
  const activePlanInfo = plans.find((p) => p.id === currentPlan);

  return (
    <div className="billing-hub">
      <header className="billing-header">
        <div>
          <h2>Billing &amp; Yearly Subscription</h2>
          <p className="muted">
            All LedgerPilot plans are billed annually (2 months free). Manage
            your yearly plan, download Stripe invoices, or update cancellation
            settings anytime.
          </p>
        </div>
        {isOwner && sub?.customer && (
          <Button
            variant="secondary"
            busy={reconcile.isPending}
            onClick={() => reconcile.mutate(undefined)}
          >
            <RefreshCw size={15} /> Sync with Stripe
          </Button>
        )}
      </header>

      <ErrorNotice
        error={
          subQuery.error ||
          plansQuery.error ||
          invoicesQuery.error ||
          checkout.error ||
          changePlan.error ||
          cancelSub.error ||
          resumeSub.error ||
          reconcile.error
        }
      />

      {returnNotice && (
        <div
          role="status"
          className={
            returnNotice.kind === "success"
              ? "billing-banner billing-banner-success"
              : "billing-banner billing-banner-info"
          }
        >
          <ShieldCheck size={18} />
          <span>{returnNotice.text}</span>
        </div>
      )}

      {subQuery.isLoading ? (
        <div className="billing-hero-card">
          <p className="muted">Loading your subscription details…</p>
        </div>
      ) : (
        <>
          <section className="billing-hero-card" aria-label="Current plan">
            <div className="billing-hero-top">
              <div>
                <span className="billing-kicker">CURRENT WORKSPACE PLAN</span>
                <div className="billing-hero-title-row">
                  <h3 className="billing-plan-name">
                    {hasActiveSub
                      ? `${activePlanInfo?.name || currentPlan} Yearly`
                      : "No Active Yearly Plan"}
                  </h3>
                  <span className={`status-pill status-pill-${badge.tone}`}>
                    {badge.label}
                  </span>
                  <span
                    className={`status-pill ${paidAccess ? "status-pill-success" : "status-pill-muted"}`}
                  >
                    {paidAccess
                      ? "All Features Unlocked"
                      : "Read & Export Only"}
                  </span>
                </div>
              </div>
              {hasActiveSub && (
                <div className="billing-hero-price">
                  <strong>
                    {formatCurrency(sub?.amount, sub?.currency || "usd")}
                  </strong>
                  <span>/ year</span>
                </div>
              )}
            </div>

            {status === "past_due" && (
              <div className="billing-alert-warning">
                <AlertTriangle size={17} />
                <span>
                  {isGraceActive
                    ? `Payment failed — grace access remains active until ${new Date((graceUntil || 0) * 1000).toLocaleString()}. Pay your latest open invoice to keep all features unlocked.`
                    : "Grace period expired. Financial posting is paused while exports and privacy tools remain accessible."}
                </span>
              </div>
            )}

            <dl className="billing-meta-grid">
              <div>
                <dt>Billing cycle</dt>
                <dd>
                  {hasActiveSub ? "Annual (billed yearly)" : "Not active"}
                </dd>
              </div>
              <div>
                <dt>{cancelAtPeriodEnd ? "Access ends on" : "Next renewal"}</dt>
                <dd>
                  {hasActiveSub
                    ? formatUnixDate(sub?.current_period_end)
                    : "Choose a plan below"}
                </dd>
              </div>
              <div>
                <dt>Email receipts</dt>
                <dd>Sent automatically on every payment &amp; change</dd>
              </div>
              <div>
                <dt>Subscription reference</dt>
                <dd className="mono-ref">
                  {sub?.subscription_id ? sub.subscription_id : "—"}
                </dd>
              </div>
            </dl>

            <div className="billing-hero-actions">
              {sub?.latest_invoice_url && (
                <a
                  className="btn secondary"
                  href={sub.latest_invoice_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <ExternalLink size={15} /> View Latest Invoice
                </a>
              )}
              {sub?.latest_invoice_pdf && (
                <a
                  className="btn secondary"
                  href={sub.latest_invoice_pdf}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Download size={15} /> Download Invoice PDF
                </a>
              )}
              {isOwner && hasActiveSub && cancelAtPeriodEnd && (
                <Button
                  busy={resumeSub.isPending}
                  onClick={() => resumeSub.mutate()}
                >
                  <RotateCcw size={15} /> Resume Yearly Subscription
                </Button>
              )}
              {isOwner && hasActiveSub && !cancelAtPeriodEnd && (
                <Button
                  variant="danger"
                  busy={cancelSub.isPending}
                  onClick={() => setCancelModalOpen(true)}
                >
                  <XCircle size={15} /> Cancel Subscription
                </Button>
              )}
            </div>

            {!isOwner && (
              <p className="muted billing-owner-note">
                Only workspace owners can purchase, switch, or cancel plans.
                Accountants can view status and download invoices.
              </p>
            )}
          </section>

          <section className="billing-plans-section" aria-label="Yearly plans">
            <div className="billing-section-head">
              <div>
                <h3>Choose a Yearly Plan</h3>
                <p className="muted">
                  Every paid plan unlocks all LedgerPilot modules immediately
                  after Stripe Checkout.
                </p>
              </div>
              <Badge>
                <Sparkles size={13} /> Annual billing · 2 months free
              </Badge>
            </div>

            <div className="plan-grid">
              {plans.map((plan) => {
                const isCurrent = hasActiveSub && currentPlan === plan.id;
                const isPopular = plan.id === "growth";
                const isUpgrade =
                  hasActiveSub && PLAN_ORDER[plan.id] > PLAN_ORDER[currentPlan];
                const isSwitchingThis =
                  changePlan.isPending && changePlan.variables === plan.id;
                const isCheckingOutThis =
                  checkout.isPending && checkout.variables === plan.id;

                return (
                  <article
                    key={plan.id}
                    className={`plan-card${isPopular ? " plan-card-featured" : ""}${isCurrent ? " plan-card-current" : ""}`}
                  >
                    <div className="plan-card-top">
                      <div className="plan-badge-row">
                        <span
                          className={`plan-pill${isPopular ? " plan-pill-accent" : ""}`}
                        >
                          {plan.badge}
                        </span>
                        {isCurrent && (
                          <span className="plan-pill plan-pill-current">
                            Current Plan
                          </span>
                        )}
                      </div>
                      <h4>{plan.name}</h4>
                      <p className="muted plan-tagline">{plan.tagline}</p>
                      <div className="plan-price">
                        <span className="plan-price-amount">
                          {plan.price_display}
                        </span>
                        <span className="plan-price-period">/ year</span>
                      </div>
                      <p className="plan-equiv">{plan.monthly_equivalent}</p>
                    </div>

                    <ul className="plan-features">
                      {plan.features.map((feat) => (
                        <li key={feat}>
                          <Check size={15} className="plan-check" />
                          <span>{feat}</span>
                        </li>
                      ))}
                    </ul>

                    <div className="plan-card-footer">
                      {!isOwner ? (
                        <Button variant="secondary" disabled>
                          Owner manages billing
                        </Button>
                      ) : isCurrent ? (
                        <Button variant="secondary" disabled>
                          <Check size={15} /> Current Active Plan
                        </Button>
                      ) : hasActiveSub ? (
                        <Button
                          variant={isUpgrade ? "primary" : "secondary"}
                          busy={isSwitchingThis}
                          disabled={changePlan.isPending}
                          onClick={() => {
                            if (
                              window.confirm(
                                `${isUpgrade ? "Upgrade" : "Switch"} to ${plan.name} (${plan.price_display}/year)? Stripe will prorate the difference and email your updated invoice.`,
                              )
                            ) {
                              changePlan.mutate(plan.id);
                            }
                          }}
                        >
                          {isUpgrade
                            ? `Upgrade to ${plan.name}`
                            : `Switch to ${plan.name}`}
                          <ArrowRight size={15} />
                        </Button>
                      ) : (
                        <Button
                          variant={isPopular ? "primary" : "secondary"}
                          busy={isCheckingOutThis}
                          disabled={checkout.isPending}
                          onClick={() => checkout.mutate(plan.id)}
                        >
                          <CreditCard size={15} /> Purchase {plan.name} Yearly
                        </Button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>

          <section
            className="billing-invoices-section"
            aria-label="Invoices and receipts"
          >
            <div className="billing-section-head">
              <div>
                <h3>Invoices &amp; Payment Receipts</h3>
                <p className="muted">
                  View hosted Stripe receipts or download PDF invoices for your
                  records.
                </p>
              </div>
            </div>

            {!sub?.customer ? (
              <div className="billing-empty-invoices">
                <FileText size={24} />
                <p>
                  No invoices yet. Once you purchase a yearly plan via Stripe
                  Checkout, your invoices and downloadable PDFs will appear
                  here.
                </p>
              </div>
            ) : invoicesQuery.isLoading ? (
              <p className="muted">Loading invoices from Stripe…</p>
            ) : (invoicesQuery.data?.items.length ?? 0) === 0 ? (
              <div className="billing-empty-invoices">
                <FileText size={24} />
                <p>
                  No Stripe invoices found for this workspace yet. Click{" "}
                  <strong>Sync with Stripe</strong> if you just completed
                  Checkout.
                </p>
              </div>
            ) : (
              <div className="perm-table-wrap">
                <table className="perm-table billing-invoice-table">
                  <thead>
                    <tr>
                      <th>Invoice</th>
                      <th>Date</th>
                      <th>Description</th>
                      <th>Amount</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invoicesQuery.data?.items.map((inv) => (
                      <tr key={inv.id}>
                        <td className="mono-ref">
                          {inv.number || inv.id.slice(0, 14)}
                        </td>
                        <td>{formatUnixDate(inv.created)}</td>
                        <td>
                          {inv.description || "LedgerPilot Yearly Subscription"}
                        </td>
                        <td>
                          <strong>
                            {formatCurrency(
                              inv.amount_paid || inv.amount_due,
                              inv.currency,
                            )}
                          </strong>
                        </td>
                        <td>
                          <span
                            className={`status-pill ${
                              inv.status === "paid"
                                ? "status-pill-success"
                                : inv.status === "open"
                                  ? "status-pill-warning"
                                  : "status-pill-muted"
                            }`}
                          >
                            {(inv.status || "issued").toUpperCase()}
                          </span>
                        </td>
                        <td>
                          <div className="invoice-actions">
                            {inv.hosted_invoice_url && (
                              <a
                                className="invoice-link"
                                href={inv.hosted_invoice_url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                <ExternalLink size={14} /> View
                              </a>
                            )}
                            {inv.invoice_pdf && (
                              <a
                                className="invoice-link"
                                href={inv.invoice_pdf}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                <Download size={14} /> PDF
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      <Modal
        open={cancelModalOpen}
        onClose={() => setCancelModalOpen(false)}
        title="Cancel your yearly subscription?"
        description="Choose whether to keep full access until the end of your paid year or stop immediately. We’ll email a cancellation confirmation."
      >
        <div className="cancel-options">
          <div className="cancel-option-card">
            <h4>Cancel at period end (Recommended)</h4>
            <p className="muted">
              Keep full access to all LedgerPilot features until{" "}
              <strong>{formatUnixDate(sub?.current_period_end)}</strong>. Your
              subscription will not renew, and you can resume anytime before
              then.
            </p>
            <Button
              variant="secondary"
              busy={cancelSub.isPending && cancelSub.variables === false}
              disabled={cancelSub.isPending}
              onClick={() => cancelSub.mutate(false)}
            >
              Cancel at end of billing year
            </Button>
          </div>
          <div className="cancel-option-card cancel-option-danger">
            <h4>Cancel immediately</h4>
            <p className="muted">
              Ends your subscription right now and switches the workspace to
              read/export-only mode. Existing accounting records and exports
              remain preserved.
            </p>
            <Button
              variant="danger"
              busy={cancelSub.isPending && cancelSub.variables === true}
              disabled={cancelSub.isPending}
              onClick={() => cancelSub.mutate(true)}
            >
              Cancel subscription immediately
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
