import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Building2, Check, Plus, ShieldCheck } from "lucide-react";
import { api, result, keys, queryClient } from "../api/runtime";
import {
  Button,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Badge,
} from "../components/ui";
import type { components } from "../api/generated/schema";

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
export function BusinessChooser({
  identity,
  onChoose,
}: {
  identity: components["schemas"]["IdentityRead"];
  onChoose: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState<string | undefined>();
  const businesses = useQuery({
    queryKey: [...keys.businesses, cursor],
    queryFn: ({ signal }) =>
      result(
        api.GET("/api/v1/businesses", {
          signal,
          params: { query: { limit: 50, cursor } },
        }),
      ),
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
          },
        }),
      );
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: keys.businesses });
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
  return (
    <main className="chooser">
      <span className="brand">
        <span className="brand-mark">L</span>LedgerPilot
      </span>
      <p className="eyebrow">ONE WORKSPACE. A CLEARER PICTURE.</p>
      <h1>Where would you like to work?</h1>
      <p className="muted">Choose a business to open its books.</p>
      <ErrorNotice
        error={businesses.error}
        retry={() => {
          void businesses.refetch();
        }}
      />
      {businesses.isPending ? (
        <Loading />
      ) : (
        <div className="business-grid">
          {businesses.data?.items.map((b) => (
            <button
              className="business-card"
              key={b.id}
              onClick={() => onChoose(b.id)}
            >
              <Building2 />
              <h3>{b.name}</h3>
              <Badge>
                {b.currency} · {b.basis}
              </Badge>
            </button>
          ))}
          <button className="business-card add" onClick={() => setOpen(true)}>
            <Plus />
            <h3>Create a business</h3>
            <p>Start with a clean set of books.</p>
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
                <option>AED</option>
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
          <Field label="Timezone">
            <input
              name="timezone"
              defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone}
              required
            />
          </Field>
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
