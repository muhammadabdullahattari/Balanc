import { PortfolioTriage } from "./portfolio-triage";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Building2,
  Check,
  CreditCard,
  Download,
  ExternalLink,
  Plus,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { api, result, keys, queryClient } from "../api/runtime";
import { TimezoneSelect } from "../components/timezone-select";
import {
  Button,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Badge,
} from "../components/ui";
import type { components } from "../api/generated/schema";

type PlanKey = "starter" | "growth" | "enterprise";

export function MFASetup({ onComplete }: { onComplete: () => void }) {
  const [code, setCode] = useState("");
  const enroll = useMutation({
    mutationFn: () => result(api.POST("/api/v1/auth/mfa/enroll")),
  });
  const confirm = useMutation({
    mutationFn: () =>
      result(api.POST("/api/v1/auth/mfa/confirm", { body: { code } })),
  });
  return (
    <section className="setup-card">
      <ShieldCheck size={30} />
      <h2>Protect your financial workspace.</h2>
      <p className="muted">
        Add an authenticator before creating or managing a business.
      </p>
      <ErrorNotice error={enroll.error || confirm.error} />
      {confirm.data ? (
        <>
          <h3>Save your recovery codes</h3>
          <p>
            Each code works once. Keep these somewhere private; they won’t be
            shown again.
          </p>
          <div className="recovery-codes">
            {confirm.data.recovery_codes.map((c) => (
              <code key={c}>{c}</code>
            ))}
          </div>
          <Button onClick={onComplete}>
            I've saved my codes <Check size={16} />
          </Button>
        </>
      ) : enroll.data ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            confirm.mutate();
          }}
        >
          <p>
            Scan this QR code with your authenticator app, or enter the setup
            key below.
          </p>
          <div className="mfa-qr">
            <QRCodeSVG
              value={enroll.data.provisioning_uri}
              size={192}
              level="M"
              title="Scan to add LedgerPilot to your authenticator"
            />
          </div>
          <code className="setup-key">{enroll.data.secret}</code>
          <Field label="Six-digit authenticator code">
            <input
              autoComplete="one-time-code"
              inputMode="numeric"
              required
              pattern="[0-9]{6}"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </Field>
          <Button busy={confirm.isPending}>Verify authenticator</Button>
        </form>
      ) : (
        <Button busy={enroll.isPending} onClick={() => enroll.mutate()}>
          Set up authenticator
        </Button>
      )}
    </section>
  );
}
function PortfolioBankReview({ businessId }: { businessId: string }) {
  const review = useQuery({
    queryKey: [...keys.business(businessId), "portfolio-bank-review"],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses/{business_id}/portfolio-status", {
          signal,
          params: {
            path: { business_id: businessId },
          },
        }),
      ),
    staleTime: 0,
  });
  if (review.isPending) return <Loading />;
  if (review.error)
    return (
      <ErrorNotice error={review.error} retry={() => void review.refetch()} />
    );
  const status = review.data;
  return (
    <dl className="portfolio-status-card">
      <dt>Bank transactions awaiting review</dt>
      <dd>{status.pending_bank_rows}</dd>
      {status.oldest_transaction && (
        <>
          <dt>Oldest transaction awaiting review</dt>
          <dd>{status.oldest_transaction}</dd>
        </>
      )}
      <dt>Latest reconciled statement</dt>
      <dd>{status.last_statement ?? "None recorded"}</dd>
      <dt>Past-period tasks awaiting review</dt>
      <dd>{status.pending_period_reviews}</dd>
      <dt>Bills without linked documents</dt>
      <dd>{status.bills_without_evidence}</dd>
    </dl>
  );
}
export function BusinessChooser({
  identity,
  onChoose,
  initialOnboardingSessionId,
  initialOnboardingCanceled,
  onClearOnboardingReturn,
}: {
  identity: components["schemas"]["IdentityRead"];
  onChoose: (id: string) => void;
  initialOnboardingSessionId?: string;
  initialOnboardingCanceled?: boolean;
  onClearOnboardingReturn?: () => void;
}) {
  const [search, setSearch] = useState("");
  const [portfolio, setPortfolio] = useState(false);
  const [open, setOpen] = useState(false);
  const [showPlanStep, setShowPlanStep] = useState(
    Boolean(initialOnboardingCanceled),
  );
  const [canceledNotice, setCanceledNotice] = useState(
    Boolean(initialOnboardingCanceled),
  );
  const [cursor, setCursor] = useState<string | undefined>();

  const businesses = useQuery({
    queryKey: [...keys.businesses, identity.id, cursor, portfolio, search],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses", {
          signal,
          params: {
            query: {
              limit: portfolio ? 5 : 50,
              cursor,
              sort: portfolio ? "name" : "id",
              search: portfolio ? search : "",
            },
          },
        }),
      ),
  });

  const plansQuery = useQuery({
    queryKey: ["billing", "plans"],
    retry: false,
    enabled: identity.mfa,
    queryFn: ({ signal }) =>
      result(api.GET("/api/v1/billing/plans", { signal })),
  });

  const onboardingStatus = useQuery({
    queryKey: [
      "billing",
      "onboarding",
      identity.id,
      initialOnboardingSessionId || "latest",
    ],
    retry: false,
    enabled:
      identity.mfa &&
      (Boolean(initialOnboardingSessionId) ||
        Boolean(plansQuery.data?.require_plan_before_business)),
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/billing/onboarding/status", {
          signal,
          params: initialOnboardingSessionId
            ? { query: { session_id: initialOnboardingSessionId } }
            : undefined,
        }),
      ),
  });

  const requirePlanBeforeBusiness = Boolean(
    plansQuery.data?.require_plan_before_business,
  );
  const paidOnboarding = onboardingStatus.data?.has_paid_plan
    ? onboardingStatus.data
    : null;

  useEffect(() => {
    if (initialOnboardingSessionId && paidOnboarding) {
      setShowPlanStep(false);
      setOpen(true);
    }
  }, [initialOnboardingSessionId, paidOnboarding]);

  const startOnboardingCheckout = useMutation({
    mutationFn: (plan: PlanKey) =>
      result(
        api.POST("/api/v1/billing/onboarding/checkout", {
          params: { query: { plan } },
        }),
      ),
    onSuccess: (data) => {
      if (data.url) {
        window.location.assign(data.url);
      }
    },
  });

  const create = useMutation({
    mutationFn: (form: HTMLFormElement) => {
      const v = new FormData(form);
      return result(
        api.POST("/api/v1/businesses", {
          body: {
            fiscal_month: Number(v.get("fiscal_month")),
            name: String(v.get("name")),
            currency: String(v.get("currency")),
            timezone: String(v.get("timezone")),
            jurisdiction: String(v.get("jurisdiction")),
            basis: v.get("basis") === "cash" ? "cash" : "accrual",
            ...(paidOnboarding?.checkout_session_id
              ? { checkout_session_id: paidOnboarding.checkout_session_id }
              : {}),
          },
        }),
      );
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: keys.businesses });
      void queryClient.invalidateQueries({
        queryKey: ["billing", "onboarding"],
      });
      onClearOnboardingReturn?.();
      onChoose(data.id);
    },
  });

  const invitation = useMutation({
    mutationFn: (token: string) =>
      result(api.POST("/api/v1/invitations/accept", { body: { token } })),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: keys.businesses });
      onChoose(data.business_id);
    },
  });

  if (!identity.mfa)
    return (
      <MFASetup
        onComplete={() => {
          void queryClient.invalidateQueries({ queryKey: keys.session });
        }}
      />
    );

  if (initialOnboardingSessionId && onboardingStatus.isPending) {
    return (
      <main className="chooser">
        <span className="brand">
          <span className="brand-mark">L</span>LedgerPilot
        </span>
        <h2>Confirming your yearly subscription…</h2>
        <p className="muted">
          Verifying your Stripe payment and preparing your invoice receipt.
        </p>
        <Loading />
      </main>
    );
  }

  if (showPlanStep && !paidOnboarding) {
    const plans = plansQuery.data?.plans || [];
    return (
      <main className="chooser chooser-plans">
        <div className="onboarding-plans-top">
          <Button
            variant="ghost"
            onClick={() => {
              setShowPlanStep(false);
              setCanceledNotice(false);
              onClearOnboardingReturn?.();
            }}
          >
            <ArrowLeft size={15} /> Back to workspaces
          </Button>
          <span className="brand">
            <span className="brand-mark">L</span>LedgerPilot
          </span>
        </div>

        <ol className="onboarding-steps" aria-label="Setup steps">
          <li className="onboarding-step onboarding-step-active">
            <span>1</span> Choose Yearly Plan
          </li>
          <li className="onboarding-step">
            <span>2</span> Stripe Checkout
          </li>
          <li className="onboarding-step">
            <span>3</span> Create Business
          </li>
        </ol>

        <div className="onboarding-plans-hero">
          <p className="eyebrow">YEARLY SUBSCRIPTION REQUIRED</p>
          <h1>Select your yearly plan before creating a business</h1>
          <p className="muted">
            All plans are billed yearly (save 2 months) and unlock every
            LedgerPilot accounting, reconciliation, reporting, and compliance
            feature immediately after checkout.
          </p>
        </div>

        {canceledNotice && (
          <div role="status" className="billing-banner billing-banner-info">
            <ShieldCheck size={18} />
            <span>
              Checkout was canceled and no charge was made. Select any yearly
              plan below when you’re ready to proceed.
            </span>
          </div>
        )}

        <ErrorNotice
          error={startOnboardingCheckout.error || plansQuery.error}
        />

        <div className="plan-grid">
          {plans.map((plan) => {
            const isPopular = plan.id === "growth";
            const busyThis =
              startOnboardingCheckout.isPending &&
              startOnboardingCheckout.variables === plan.id;
            return (
              <article
                key={plan.id}
                className={`plan-card${isPopular ? " plan-card-featured" : ""}`}
              >
                <div className="plan-card-top">
                  <div className="plan-badge-row">
                    <span
                      className={`plan-pill${isPopular ? " plan-pill-accent" : ""}`}
                    >
                      {plan.badge}
                    </span>
                    <span className="plan-pill">Yearly Plan</span>
                  </div>
                  <h3>{plan.name}</h3>
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
                  <Button
                    variant={isPopular ? "primary" : "secondary"}
                    busy={busyThis}
                    disabled={startOnboardingCheckout.isPending}
                    onClick={() => startOnboardingCheckout.mutate(plan.id)}
                  >
                    <CreditCard size={15} /> Purchase {plan.name} Yearly
                  </Button>
                </div>
              </article>
            );
          })}
        </div>

        <p className="muted onboarding-plans-footnote">
          <Sparkles size={14} /> Powered by Stripe Checkout. You’ll receive an
          email confirmation and downloadable invoice PDF as soon as payment
          completes, and you can upgrade or cancel anytime from Settings →
          Billing.
        </p>
      </main>
    );
  }

  const handleCreateClick = () => {
    if (requirePlanBeforeBusiness && !paidOnboarding) {
      setShowPlanStep(true);
    } else {
      setOpen(true);
    }
  };

  return (
    <main className="chooser">
      <span className="brand">
        <span className="brand-mark">L</span>LedgerPilot
      </span>
      <p className="eyebrow">ONE WORKSPACE. A CLEARER PICTURE.</p>
      <h1>Where would you like to work?</h1>
      <p className="muted">Choose a business to open its books.</p>
      {paidOnboarding && (
        <div role="status" className="billing-banner billing-banner-success">
          <ShieldCheck size={18} />
          <span>
            Your{" "}
            <strong style={{ textTransform: "capitalize" }}>
              {paidOnboarding.plan} Yearly
            </strong>{" "}
            plan is paid and ready! Click{" "}
            <strong>Complete business setup</strong> below to launch your
            workspace with all features unlocked.
          </span>
        </div>
      )}
      <ErrorNotice
        error={businesses.error}
        retry={() => {
          void businesses.refetch();
        }}
      />
      <div className="chooser-controls">
        <label className="chooser-checkbox">
          <input
            type="checkbox"
            checked={portfolio}
            onChange={(e) => {
              setCursor(undefined);
              setPortfolio(e.target.checked);
            }}
          />{" "}
          Show portfolio review status
        </label>
        {portfolio && (
          <Field label="Find a business by name">
            <input
              type="search"
              maxLength={100}
              placeholder="Filter workspaces by business name…"
              value={search}
              onChange={(e) => {
                setCursor(undefined);
                setSearch(e.target.value);
              }}
            />
          </Field>
        )}
        {portfolio && (
          <details className="chooser-details">
            <summary>Review and assign portfolio exceptions</summary>
            <PortfolioTriage />
          </details>
        )}
      </div>
      {businesses.isPending ? (
        <Loading />
      ) : (
        <div className="business-grid">
          {businesses.data?.items.map((b) => (
            <div key={b.id} className="business-card-item">
              <button className="business-card" onClick={() => onChoose(b.id)}>
                <Building2 />
                <h3>{b.name}</h3>
                <Badge>
                  {b.currency} · {b.basis}
                </Badge>
              </button>
              {portfolio && <PortfolioBankReview businessId={b.id} />}
            </div>
          ))}
          <button className="business-card add" onClick={handleCreateClick}>
            <Plus />
            <h3>
              {paidOnboarding ? "Complete business setup" : "Create a business"}
            </h3>
            <p>
              {paidOnboarding
                ? `Finish setup to activate your ${paidOnboarding.plan} yearly plan.`
                : requirePlanBeforeBusiness
                  ? "Choose a yearly plan to unlock your new workspace."
                  : "Start with a clean set of books."}
            </p>
          </button>
        </div>
      )}
      {businesses.data?.next_cursor && (
        <Button
          variant="secondary"
          onClick={() => setCursor(businesses.data?.next_cursor || undefined)}
        >
          Next businesses
        </Button>
      )}
      {cursor && (
        <Button variant="ghost" onClick={() => setCursor(undefined)}>
          First page
        </Button>
      )}
      <details className="invitation">
        <summary>Have an invitation?</summary>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            invitation.mutate(
              String(new FormData(e.currentTarget).get("token")),
            );
          }}
        >
          <Field label="Invitation token">
            <input name="token" required minLength={16} />
          </Field>
          <ErrorNotice error={invitation.error} />
          <Button busy={invitation.isPending}>Join business</Button>
        </form>
      </details>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Create your business"
        description="These settings determine how your financial reports are prepared."
      >
        {paidOnboarding && (
          <div className="onboarding-paid-box">
            <div className="onboarding-paid-head">
              <ShieldCheck size={18} />
              <div>
                <strong style={{ textTransform: "capitalize" }}>
                  {paidOnboarding.plan} Yearly Plan Confirmed
                </strong>
                <p className="muted">
                  Payment succeeded and a confirmation email has been sent. All
                  features will be unlocked as soon as you create your business.
                </p>
              </div>
            </div>
            {(paidOnboarding.invoice_url || paidOnboarding.invoice_pdf) && (
              <div className="invoice-actions">
                {paidOnboarding.invoice_url && (
                  <a
                    className="invoice-link"
                    href={paidOnboarding.invoice_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink size={14} /> View Invoice
                  </a>
                )}
                {paidOnboarding.invoice_pdf && (
                  <a
                    className="invoice-link"
                    href={paidOnboarding.invoice_pdf}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <Download size={14} /> Download PDF
                  </a>
                )}
              </div>
            )}
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(e.currentTarget);
          }}
        >
          <ErrorNotice error={create.error} />
          <Field label="Business name">
            <input name="name" required maxLength={120} />
          </Field>
          <div className="form-grid">
            <Field label="Base currency">
              <select name="currency">
                <option>USD</option>
                <option>PKR</option>
                <option>GBP</option>
                <option>EUR</option>
                <option>CAD</option>
                <option>AUD</option>
              </select>
            </Field>
            <Field label="Accounting basis">
              <select name="basis">
                <option value="accrual">Accrual</option>
                <option value="cash">Cash</option>
              </select>
            </Field>
          </div>
          <Field label="Fiscal year starts">
            <select name="fiscal_month" defaultValue="1">
              {Array.from({ length: 12 }, (_, i) => (
                <option key={i + 1} value={i + 1}>
                  {new Intl.DateTimeFormat("en", {
                    month: "long",
                    timeZone: "UTC",
                  }).format(new Date(Date.UTC(2026, i, 1)))}
                </option>
              ))}
            </select>
          </Field>
          <TimezoneSelect name="timezone" label="Timezone" required />
          <Field
            label="Jurisdiction / accounting policy"
            hint="Use the policy reviewed by your accountant."
          >
            <input name="jurisdiction" required maxLength={100} />
          </Field>
          <Button busy={create.isPending}>Create business</Button>
        </form>
      </Modal>
    </main>
  );
}
