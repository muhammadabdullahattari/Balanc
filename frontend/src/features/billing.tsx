import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, keys, queryClient, result } from "../api/runtime";
import { Badge, Button, ErrorNotice, Field } from "../components/ui";
import { useBusiness } from "./context";

export function BillingSettings() {
  const b = useBusiness();
  const path = { business_id: b.id };
  const [selectedPlan, setSelectedPlan] = useState<
    "starter" | "growth" | "enterprise"
  >("growth");

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

  const changePlan = useMutation({
    mutationFn: (plan: "starter" | "growth" | "enterprise") =>
      result(
        api.POST("/api/v1/businesses/{business_id}/billing/subscription/plan", {
          params: { path, query: { new_plan: plan } },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "billing", "subscription"],
      });
    },
  });

  const cancelSub = useMutation({
    mutationFn: () =>
      result(
        api.POST(
          "/api/v1/businesses/{business_id}/billing/subscription/cancel",
          {
            params: { path },
          },
        ),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...keys.business(b.id), "billing", "subscription"],
      });
    },
  });

  const checkout = useMutation({
    mutationFn: (plan: "starter" | "growth" | "enterprise") =>
      result(
        api.POST("/api/v1/businesses/{business_id}/billing/checkout", {
          params: { path, query: { plan } },
        }),
      ),
    onSuccess: (data) => {
      if (data && typeof data === "object" && "checkout_url" in data) {
        window.location.href = String(data.checkout_url);
      }
    },
  });

  const sub = subQuery.data?.snapshot as
    | {
        status?: string;
        plan?: string;
        paid_access?: boolean;
        grace_until?: number | null;
        subscription_id?: string;
      }
    | undefined;

  const currentPlan = (sub?.plan || "starter") as
    "starter" | "growth" | "enterprise";
  const status = sub?.status || "inactive";
  const paidAccess = Boolean(sub?.paid_access);
  const graceUntil = sub?.grace_until;
  const isGraceActive = graceUntil && Date.now() < graceUntil * 1000;

  return (
    <div className="billing-settings">
      <h2>Subscription & Entitlements</h2>
      <p className="muted">
        Manage commercial plan tiers, Stripe billing lifecycle, and operational
        entitlements.
      </p>

      <ErrorNotice
        error={
          subQuery.error ||
          changePlan.error ||
          cancelSub.error ||
          checkout.error
        }
      />

      {subQuery.isLoading ? (
        <p>Loading subscription details...</p>
      ) : (
        <>
          <div className="review-summary" style={{ marginBottom: "1.5rem" }}>
            <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
              <strong>Current Status:</strong>
              <Badge>{status.toUpperCase()}</Badge>
              {paidAccess ? (
                <Badge>PAID ACCESS ACTIVE</Badge>
              ) : (
                <Badge>READ / EXPORT ONLY</Badge>
              )}
            </div>

            {status === "past_due" && (
              <p className="warning-text" style={{ marginTop: "0.5rem" }}>
                {isGraceActive
                  ? `Grace period active until ${new Date((graceUntil || 0) * 1000).toLocaleString()}. Update your payment method before expiration.`
                  : "Grace period expired. State-changing financial entries are locked. Export snapshots and privacy review remain accessible."}
              </p>
            )}

            <div style={{ marginTop: "0.5rem" }}>
              <strong>Current Tier:</strong>{" "}
              <span style={{ textTransform: "capitalize" }}>{currentPlan}</span>
            </div>
            {sub?.subscription_id && (
              <div
                style={{
                  marginTop: "0.25rem",
                  fontSize: "0.85rem",
                  color: "#888",
                }}
              >
                Stripe Subscription ID: {sub.subscription_id}
              </div>
            )}
          </div>

          <h3>Change Plan Tier</h3>
          <div
            className="form-grid"
            style={{ maxWidth: "400px", marginBottom: "1rem" }}
          >
            <Field label="Target Tier">
              <select
                value={selectedPlan}
                onChange={(e) =>
                  setSelectedPlan(
                    e.target.value as "starter" | "growth" | "enterprise",
                  )
                }
              >
                <option value="starter">Starter</option>
                <option value="growth">Growth</option>
                <option value="enterprise">Enterprise</option>
              </select>
            </Field>
          </div>

          <div
            style={{
              display: "flex",
              gap: "0.75rem",
              flexWrap: "wrap",
              marginBottom: "2rem",
            }}
          >
            <Button
              busy={changePlan.isPending}
              disabled={selectedPlan === currentPlan}
              onClick={() => changePlan.mutate(selectedPlan)}
            >
              Update Subscription Plan
            </Button>
            <Button
              variant="secondary"
              busy={checkout.isPending}
              onClick={() => checkout.mutate(selectedPlan)}
            >
              Start New Stripe Checkout
            </Button>
            {status === "active" && (
              <Button
                variant="danger"
                busy={cancelSub.isPending}
                onClick={() => {
                  if (
                    window.confirm(
                      "Cancel this subscription at the end of the current billing period?",
                    )
                  ) {
                    cancelSub.mutate();
                  }
                }}
              >
                Cancel Subscription
              </Button>
            )}
          </div>

          {changePlan.isSuccess && (
            <p role="status" className="success-text">
              Plan update initiated successfully. Entitlements will synchronize
              with Stripe.
            </p>
          )}
          {cancelSub.isSuccess && (
            <p role="status" className="success-text">
              Subscription cancellation scheduled for period end.
            </p>
          )}
        </>
      )}
    </div>
  );
}
