import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button, ErrorNotice, Field } from "../components/ui";

export type SignInFactor = { mfa_code: string } | { recovery_code: string };

export function SignInVerification({
  description,
  error,
  busy,
  expired = false,
  onVerify,
  restart,
  clearError,
}: {
  description: string;
  error: unknown;
  busy: boolean;
  expired?: boolean;
  onVerify: (factor: SignInFactor) => void;
  restart: () => void;
  clearError: () => void;
}) {
  const [method, setMethod] = useState<"authenticator" | "recovery">(
    "authenticator",
  );
  const [code, setCode] = useState("");
  const codeInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!expired && !busy) codeInput.current?.focus();
  }, [expired, method, busy]);
  function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || expired) return;
    const value = code.trim();
    if (!value) return;
    setCode("");
    onVerify(
      method === "authenticator"
        ? { mfa_code: value }
        : { recovery_code: value },
    );
  }
  return (
    <section aria-labelledby="sign-in-verification-title">
      <p className="eyebrow">TWO-STEP VERIFICATION</p>
      <h1 id="sign-in-verification-title">Verify your sign-in</h1>
      <p className="muted">{description}</p>
      <ErrorNotice error={error} />
      {!expired && (
        <form onSubmit={verify}>
          <Field
            label={
              method === "authenticator"
                ? "Authenticator code"
                : "Recovery code"
            }
          >
            <input
              ref={codeInput}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              required
              pattern={method === "authenticator" ? "[0-9]{6}" : undefined}
              maxLength={method === "authenticator" ? 6 : 128}
              inputMode={method === "authenticator" ? "numeric" : "text"}
              autoComplete={
                method === "authenticator" ? "one-time-code" : "off"
              }
            />
          </Field>
          <Button busy={busy} type="submit">
            Verify and sign in
          </Button>
        </form>
      )}
      <div className="auth-links">
        {!expired && (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setMethod(
                method === "authenticator" ? "recovery" : "authenticator",
              );
              setCode("");
              clearError();
            }}
          >
            {method === "authenticator"
              ? "Use a recovery code"
              : "Use an authenticator code"}
          </button>
        )}
        <button type="button" disabled={busy} onClick={restart}>
          Back to sign in
        </button>
      </div>
    </section>
  );
}
