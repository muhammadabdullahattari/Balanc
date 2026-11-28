import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, result } from "../api/runtime";
import { Badge, Button, ErrorNotice } from "../components/ui";
import { useBusiness } from "./context";

type TabId = "schedules" | "close" | "chasing" | "connectors" | "controls";

export function AutonomousOffice() {
  const { id } = useBusiness();
  const [tab, setTab] = useState<TabId>("schedules");
  const [merchantInput, setMerchantInput] = useState("AMZN MKTP US*2K4");
  const [merchantOutput, setMerchantOutput] = useState<string | null>(null);
  const [chaseEmail, setChaseEmail] = useState("");
  const [chaseSummary, setChaseSummary] = useState("Office supplies charge");
  const [chaseOutput, setChaseOutput] = useState<string | null>(null);
  const [vapidPublicKey, setVapidPublicKey] = useState<string | null>(null);
  const [taxIdInput, setTaxIdInput] = useState("12-3456789");
  const [taxIdOutput, setTaxIdOutput] = useState<string | null>(null);

  const schedulesQuery = useQuery({
    queryKey: ["autonomous-schedules", id],
    queryFn: async () =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/autonomous/schedules", {
          params: { path: { business_id: id } },
        }),
      )) as {
        items: Array<{
          id: string;
          job_kind: string;
          cron_expression: string;
          enabled: boolean;
        }>;
      },
  });

  const policiesQuery = useQuery({
    queryKey: ["autonomous-policies", id],
    queryFn: async () =>
      (await result(
        api.GET(
          "/api/v1/businesses/{business_id}/autonomous/auto-post-policies",
          {
            params: { path: { business_id: id } },
          },
        ),
      )) as {
        items: Array<{
          job_kind: string;
          enabled: boolean;
          max_amount: string;
        }>;
      },
  });

  const accrualsQuery = useQuery({
    queryKey: ["autonomous-accruals", id],
    queryFn: async () =>
      (await result(
        api.GET("/api/v1/businesses/{business_id}/autonomous/accruals", {
          params: { path: { business_id: id } },
        }),
      )) as {
        items: Array<{
          id: string;
          description: string;
          amount: string;
          status: string;
        }>;
      },
  });

  const normalizeMerchantMut = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/banking/merchants/normalize",
          {
            params: { path: { business_id: id } },
            body: {
              raw_descriptor: merchantInput,
              amount: "-125.00",
              use_llm: false,
            },
          },
        ),
      )) as {
        normalized_name: string;
        business_category: string;
        confidence: string;
      },
    onSuccess: (data) => {
      setMerchantOutput(
        `${data.normalized_name} — ${data.business_category} (${Math.round(Number(data.confidence) * 100)}% confidence)`,
      );
    },
  });

  const sendChaseMut = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST("/api/v1/businesses/{business_id}/autonomous/chasing", {
          params: { path: { business_id: id } },
          body: {
            recipient_email: chaseEmail,
            target_entity_type: "bank_transaction",
            channel: "email",
            transaction_summary: chaseSummary,
          },
        }),
      )) as { upload_path: string },
    onSuccess: (data) => {
      setChaseOutput(
        `Email reminder staged with 7-day upload link: ${data.upload_path}`,
      );
      setChaseEmail("");
    },
  });

  const generateAlertKeysMut = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/notifications/vapid/generate",
          {
            params: { path: { business_id: id } },
          },
        ),
      )) as { public_key: string },
    onSuccess: (data) => {
      setVapidPublicKey(data.public_key);
    },
  });

  const validateTaxMut = useMutation({
    mutationFn: async () =>
      (await result(
        api.POST(
          "/api/v1/businesses/{business_id}/fraud-sentinel/validate-tax-id",
          {
            params: { path: { business_id: id } },
            body: {
              tax_id: taxIdInput,
              country_code: "US",
              check_eu_vies_online: false,
            },
          },
        ),
      )) as { status: string; detail: string },
    onSuccess: (data) => {
      setTaxIdOutput(`${data.status}: ${data.detail}`);
    },
  });

  const tabs: Array<{ id: TabId; label: string }> = [
    { id: "schedules", label: "Schedules & Auto-Post" },
    { id: "close", label: "Accruals & Amortization" },
    { id: "chasing", label: "Receipt Chasing" },
    { id: "connectors", label: "Merchant Cleanup" },
    { id: "controls", label: "Fraud & Owner Alerts" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Autonomous Finance Office</h1>
        <p className="text-sm text-stone-600">
          Manage scheduled close routines, approval caps, email receipt chasing,
          and fraud alerts.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`rounded-lg border px-3 py-1.5 text-sm font-medium ${
              tab === t.id
                ? "border-stone-900 bg-stone-900 text-white"
                : "border-stone-300 bg-white text-stone-700 hover:bg-stone-100"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "schedules" ? (
        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
            <h2 className="text-base font-semibold">Scheduled Jobs</h2>
            <p className="mb-3 text-xs text-stone-500">
              Recurring close and monitoring tasks
            </p>
            <ul className="space-y-2 text-sm">
              {(schedulesQuery.data?.items ?? []).length === 0 ? (
                <li className="text-stone-500">No schedules configured yet.</li>
              ) : (
                (schedulesQuery.data?.items ?? []).map((j) => (
                  <li key={j.id} className="flex items-center justify-between">
                    <span>{j.job_kind.replace(/_/g, " ")}</span>
                    <Badge tone={j.enabled ? "good" : "warning"}>
                      {j.enabled ? "Active" : "Paused"}
                    </Badge>
                  </li>
                ))
              )}
            </ul>
          </section>

          <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
            <h2 className="text-base font-semibold">Auto-Post Safeguards</h2>
            <p className="mb-3 text-xs text-stone-500">
              Off by default; owner approval required
            </p>
            <ul className="space-y-2 text-sm">
              {(policiesQuery.data?.items ?? []).length === 0 ? (
                <li className="text-stone-500">
                  All automated postings currently require human approval.
                </li>
              ) : (
                (policiesQuery.data?.items ?? []).map((p) => (
                  <li
                    key={p.job_kind}
                    className="flex items-center justify-between"
                  >
                    <span>{p.job_kind.replace(/_/g, " ")}</span>
                    <span>Cap: {p.max_amount}</span>
                  </li>
                ))
              )}
            </ul>
          </section>
        </div>
      ) : null}

      {tab === "close" ? (
        <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">
            Month-End Accrual Candidates
          </h2>
          <ul className="space-y-2 text-sm">
            {(accrualsQuery.data?.items ?? []).length === 0 ? (
              <li className="text-stone-500">
                No pending month-end accruals found.
              </li>
            ) : (
              (accrualsQuery.data?.items ?? []).map((a) => (
                <li
                  key={a.id}
                  className="flex items-center justify-between border-b pb-2"
                >
                  <span>{a.description}</span>
                  <span className="font-medium">{a.amount}</span>
                </li>
              ))
            )}
          </ul>
        </section>
      ) : null}

      {tab === "chasing" ? (
        <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">
            Email Receipt Chasing
          </h2>
          <div className="space-y-3">
            <p className="text-sm text-stone-600">
              Send an email reminder with a secure 7-day single-use upload link.
            </p>
            <input
              type="email"
              value={chaseEmail}
              onChange={(e) => setChaseEmail(e.target.value)}
              placeholder="teammate@company.com"
              className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
            />
            <input
              type="text"
              value={chaseSummary}
              onChange={(e) => setChaseSummary(e.target.value)}
              placeholder="Transaction summary"
              className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
            />
            {sendChaseMut.error ? (
              <ErrorNotice error={sendChaseMut.error} />
            ) : null}
            {chaseOutput ? (
              <p className="text-sm text-emerald-700">{chaseOutput}</p>
            ) : null}
            <Button
              type="button"
              disabled={sendChaseMut.isPending || !chaseEmail.includes("@")}
              onClick={() => sendChaseMut.mutate()}
            >
              Send Email Reminder
            </Button>
          </div>
        </section>
      ) : null}

      {tab === "connectors" ? (
        <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">
            Merchant Name & Category Cleanup
          </h2>
          <div className="space-y-3">
            <input
              type="text"
              value={merchantInput}
              onChange={(e) => setMerchantInput(e.target.value)}
              className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
            />
            {normalizeMerchantMut.error ? (
              <ErrorNotice error={normalizeMerchantMut.error} />
            ) : null}
            {merchantOutput ? (
              <p className="text-sm font-medium text-emerald-800">
                {merchantOutput}
              </p>
            ) : null}
            <Button
              type="button"
              disabled={normalizeMerchantMut.isPending}
              onClick={() => normalizeMerchantMut.mutate()}
            >
              Normalize Merchant
            </Button>
          </div>
        </section>
      ) : null}

      {tab === "controls" ? (
        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-base font-semibold">Owner Phone Alerts</h2>
            <div className="space-y-3">
              <p className="text-sm text-stone-600">
                Generate local browser push keys for instant high-risk vendor
                payment alerts.
              </p>
              {generateAlertKeysMut.error ? (
                <ErrorNotice error={generateAlertKeysMut.error} />
              ) : null}
              {vapidPublicKey ? (
                <p className="break-all rounded bg-stone-100 p-2 font-mono text-xs">
                  Push public key ready: {vapidPublicKey.slice(0, 32)}...
                </p>
              ) : null}
              <Button
                type="button"
                disabled={generateAlertKeysMut.isPending}
                onClick={() => generateAlertKeysMut.mutate()}
              >
                Generate Local Alert Keys
              </Button>
            </div>
          </section>

          <section className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-base font-semibold">
              Vendor Tax ID Check
            </h2>
            <div className="space-y-3">
              <input
                type="text"
                value={taxIdInput}
                onChange={(e) => setTaxIdInput(e.target.value)}
                placeholder="12-3456789"
                className="w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
              />
              {validateTaxMut.error ? (
                <ErrorNotice error={validateTaxMut.error} />
              ) : null}
              {taxIdOutput ? (
                <p className="text-sm text-stone-800">{taxIdOutput}</p>
              ) : null}
              <Button
                type="button"
                variant="secondary"
                disabled={validateTaxMut.isPending}
                onClick={() => validateTaxMut.mutate()}
              >
                Verify Tax ID
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
